import sys
import deepinv as dinv
import torch
from torch.utils.data import DataLoader
from deepinv.diffops.models.denoiser import Denoiser
from deepinv.optim.data_fidelity import *
from deepinv.pnp.pnp import PnP
from deepinv.pnp.deep_equilibrium import DEQ
from deepinv.optim.fixed_point import FixedPoint, AndersonAcceleration
from deepinv.optim.optim_iterator import *
from deepinv.training_utils import test, train
from torchvision import datasets, transforms
import os
import wandb

num_workers = 4 if torch.cuda.is_available() else 0  # set to 0 if using small cpu, else 4
problem = 'deblur'
G = 1
denoiser_name = 'dncnn'
depth = 7
ckpt_path = None
pnp_algo = 'PGD'
train_dataset_name = 'drunet'
test_dataset_name = 'cbsd68'
noise_level_img = 0.03
lamb = 10
stepsize = 1.
sigma_k = 2.
sigma_denoiser = sigma_k*noise_level_img
max_iter = 20
crit_conv = 1e-5
verbose = True
early_stop = False 
n_channels = 3
pretrain = False
epochs = 10
im_size = 32
batch_size = 1
max_datapoints = 100
use_anderson = False
wandb_vis = True
max_iter_backward = 2

if wandb_vis :
    wandb.init(project='DEQ')

if problem == 'CS':
    p = dinv.physics.CompressedSensing(m=300, img_shape=(1, 28, 28), device=dinv.device)
elif problem == 'onebitCS':
    p = dinv.physics.CompressedSensing(m=300, img_shape=(1, 28, 28), device=dinv.device)
    p.sensor_model = lambda x: torch.sign(x)
elif problem == 'inpainting':
    p = dinv.physics.Inpainting(tensor_size=(1, 28, 28), mask=.5, device=dinv.device)
elif problem == 'denoising':
    p = dinv.physics.Denoising(sigma=.2)
elif problem == 'blind_deblur':
    p = dinv.physics.BlindBlur(kernel_size=11)
elif problem == 'deblur':
    p = dinv.physics.BlurFFT((3,im_size,im_size), filter=dinv.physics.blur.gaussian_blur(sigma=(2, .1), angle=45.), device=dinv.device, noise_model = dinv.physics.GaussianNoise(sigma=noise_level_img))
else:
    raise Exception("The inverse problem chosen doesn't exist")

data_fidelity = L2()


if not os.path.exists(f'../datasets/artificial/{train_dataset_name}/dinv_dataset0.h5'):
    val_transform = transforms.Compose([
                transforms.CenterCrop(im_size),
                transforms.ToTensor(),
    ])
    train_transform = transforms.Compose([
                    transforms.RandomCrop(im_size, pad_if_needed=True),
                    transforms.RandomHorizontalFlip(p=0.5),
                    transforms.RandomVerticalFlip(p=0.5),
                    transforms.ToTensor(),
                ])
    train_input_dataset = datasets.ImageFolder(root=f'../datasets/{train_dataset_name}/', transform=train_transform)
    test_input_dataset = datasets.ImageFolder(root=f'../datasets/{test_dataset_name}/', transform=val_transform)
    dinv.datasets.generate_dataset(train_dataset=train_input_dataset, test_dataset=test_input_dataset,
                                physics=p, device=dinv.device, save_dir=f'../datasets/artificial/{train_dataset_name}/', max_datapoints=max_datapoints,
                                num_workers=num_workers)

train_dataset = dinv.datasets.HDF5Dataset(path=f'../datasets/artificial/{train_dataset_name}/dinv_dataset0.h5', train=True)
eval_dataset = dinv.datasets.HDF5Dataset(path=f'../datasets/artificial/{train_dataset_name}/dinv_dataset0.h5', train=False)
train_dataloader = DataLoader(train_dataset, batch_size=batch_size, num_workers=num_workers, shuffle=False)
eval_dataloader = DataLoader(eval_dataset, batch_size=batch_size, num_workers=num_workers, shuffle=False)

model_spec = {'name': denoiser_name,
              'args': {
                    'in_channels':n_channels, 
                    'out_channels':n_channels,
                    'depth': depth,
                    'ckpt_path': ckpt_path,
                    'pretrain':pretrain, 
                    'train': True, 
                    'device':dinv.device
                    }}
denoiser = Denoiser(model_spec=model_spec)

PnP_module = PnP(denoiser=denoiser, max_iter=max_iter, sigma_denoiser=sigma_denoiser, stepsize=stepsize, unroll=True, weight_tied=True)
iterator = PGD(prox_g=PnP_module.prox_g, data_fidelity=data_fidelity, stepsize=stepsize, device=dinv.device, update_stepsize = PnP_module.update_stepsize)
FP = FixedPoint(iterator, max_iter=max_iter, early_stop=early_stop, crit_conv=crit_conv, verbose=verbose)
FP = AndersonAcceleration(iterator, max_iter=max_iter, early_stop=early_stop, crit_conv=crit_conv, verbose=verbose)
model = DEQ(FP, iterator, PnP_module, max_iter_backward=max_iter_backward)

# choose training losses
losses = []
losses.append(dinv.loss.SupLoss(metric=dinv.metric.mse()))

# choose optimizer and scheduler
optimizer = torch.optim.Adam(PnP_module.parameters(), lr=1e-4, weight_decay=1e-8)
scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=int(epochs*.8))

train(model=model,
        train_dataloader=train_dataloader,
        eval_dataloader=eval_dataloader,
        epochs=epochs,
        scheduler=scheduler,
        loss_closure=losses,
        physics=p,
        optimizer=optimizer,
        device=dinv.device,
        ckp_interval=10,
        save_path=f'../checkpoints/tests/demo_unrolled',
        plot=False,
        plot_input=True,
        verbose=True,
        wandb_vis=wandb_vis)

