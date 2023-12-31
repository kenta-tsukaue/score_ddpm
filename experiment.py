from typing import List
import os
import torch
import torch.utils.data
import torchvision
from torchvision import datasets, transforms
from PIL import Image
import datetime
import numpy as np
from labml import lab, tracker, experiment, monit
from labml.configs import BaseConfigs, option
from labml_helpers.device import DeviceConfigs
from labml_nn.diffusion.ddpm import DenoiseDiffusion
from labml_nn.diffusion.ddpm.unet import UNet


class Configs(BaseConfigs):
    """
    ## Configurations
    """
    # Device to train the model on.
    # [`DeviceConfigs`](https://docs.labml.ai/api/helpers.html#labml_helpers.device.DeviceConfigs)
    #  picks up an available CUDA device or defaults to CPU.
    device: torch.device = DeviceConfigs()

    # U-Net model for $\textcolor{lightgreen}{\epsilon_\theta}(x_t, t)$
    eps_model: UNet
    # [DDPM algorithm](index.html)
    diffusion: DenoiseDiffusion

    # Number of channels in the image. $3$ for RGB.
    image_channels: int = 3
    # Image size
    image_size: int = 32
    # Number of channels in the initial feature map
    n_channels: int = 64
    # The list of channel numbers at each resolution.
    # The number of channels is `channel_multipliers[i] * n_channels`
    channel_multipliers: List[int] = [1, 2, 2, 4]
    # The list of booleans that indicate whether to use attention at each resolution
    is_attention: List[int] = [False, False, False, True]

    # Number of time steps $T$
    n_steps: int = 1_000
    # Batch size
    batch_size: int = 64
    # Number of samples to generate
    n_samples: int = 16
    # Learning rate
    learning_rate: float = 2e-5

    # Number of training epochs
    #epochs: int = 1_000
    epochs: int = 1_0
    # Dataset
    dataset: torch.utils.data.Dataset
    # Dataloader
    data_loader: torch.utils.data.DataLoader

    # Adam optimizer
    optimizer: torch.optim.Adam

    def init(self):
        # Create $\textcolor{lightgreen}{\epsilon_\theta}(x_t, t)$ model
        self.eps_model = UNet(
            image_channels=self.image_channels,
            n_channels=self.n_channels,
            ch_mults=self.channel_multipliers,
            is_attn=self.is_attention,
        ).to(self.device)

        # Create [DDPM class](index.html)
        self.diffusion = DenoiseDiffusion(
            eps_model=self.eps_model,
            n_steps=self.n_steps,
            device=self.device,
        )
        #データセットの定義
        # 前処理の定義
        transform = transforms.Compose([
            transforms.Resize(32),  # 画像サイズを変更する場合
            transforms.CenterCrop(32),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5])
        ])
        self.dataset = datasets.CIFAR10(root='./data', train=True, transform=transform, download=True)
        # Create dataloader
        self.data_loader = torch.utils.data.DataLoader(self.dataset, self.batch_size, shuffle=True, pin_memory=True)
        # Create optimizer
        self.optimizer = torch.optim.Adam(self.eps_model.parameters(), lr=self.learning_rate)

        # Image logging
        tracker.set_image("sample", True)

    def sample(self):
        """
        ### Sample images
        """
        # 現在のタイムスタンプを取得
        timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')

        # 保存先ディレクトリのパス
        save_dir = f"./mnt/data/images_{timestamp}/"
        
        #ディレクトリがなかったら作成
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)

        with torch.no_grad():
            # $x_T \sim p(x_T) = \mathcal{N}(x_T; \mathbf{0}, \mathbf{I})$ / x(T)を標準正規分布からサンプリング
            x = torch.randn([self.n_samples, self.image_channels, self.image_size, self.image_size],
                            device=self.device)

            # 1000ステップかけて、ノイズを除去していく。
            for t_ in monit.iterate('Sample', self.n_steps):
                # $t$
                t = self.n_steps - t_ - 1
                # /x(t)からx(t-1)を取得
                x = self.diffusion.p_sample(x, x.new_full((self.n_samples,), t, dtype=torch.long))

            # Log samples
            tracker.save('sample', x)

            # データを0~255の範囲にスケーリング
            scaled_data = ((x + 1) * 127.5).byte()

            # 画像を保存
            for i, img_tensor in enumerate(scaled_data):
                #img = Image.fromarray(img_tensor[0].numpy(), 'L')  # 'L'はグレースケール画像を意味する
                img_array = np.transpose(img_tensor.numpy(), (1, 2, 0))
                img = Image.fromarray(img_array, 'RGB')
                file_name = f"image_{i}.jpg"
                file_path = os.path.join(save_dir, file_name)
                img.save(file_path)



    def train(self):
        """
        ### Train
        """

        # Iterate through the dataset
        for inputs, labels in monit.iterate('Train', self.data_loader):#self.data_loader
            # Increment global step
            tracker.add_global_step()
            # データをデバイスに送信
            data = inputs.to(self.device)
            # 勾配を0にする
            self.optimizer.zero_grad()
            # ロスを計算する。/__init__.pyの264行目あたりから
            loss = self.diffusion.loss(data)
            # 勾配を計算する
            loss.backward()
            # 勾配を元にパラメータを更新
            self.optimizer.step()
            # Track the loss
            tracker.save('loss', loss)

    def run(self):
        """
        ### Training loop
        """
        for _ in monit.loop(self.epochs):
            # Train the model
            self.train()
            # Sample some images
            
            # New line in the console
            tracker.new_line()
            
            self.sample()

            # Save the model
            experiment.save_checkpoint()



def main():
    # Create experiment
    experiment.create(name='diffuse', writers={'screen', 'labml'})

    # Create configurations
    configs = Configs()

    # Set configurations. You can override the defaults by passing the values in the dictionary.
    experiment.configs(configs, {
        'image_channels': 3,
        'epochs': 100,
    })

    # Initialize
    configs.init()

    # Set models for saving and loading
    experiment.add_pytorch_models({'eps_model': configs.eps_model})

    # Start and run the training loop
    with experiment.start():
        configs.run()


#
if __name__ == '__main__':
    main()
