import os
import random

import torch
import torch.nn as nn
import torch.optim as optim
import torch.utils.data as data
import torchvision

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from datetime import datetime
from tqdm.notebook import tqdm_notebook

# 为了可以复现
random.seed(42)
g = torch.Generator().manual_seed(2147483647)

#确保所有操作在GPU(如果使用)上是确定的，以保证再现性
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False

device = torch.device("cuda:0") if torch.cuda.is_available() else torch.device("cpu")
print("Device:", device)

# 数据转换：变为tensor + 均值为0.5/方差为0.5
# 最后数据从（0,1）变为（-1,1）
MNIST_preprocess = torchvision.transforms.Compose([torchvision.transforms.ToTensor(),
                                                   torchvision.transforms.Normalize((0.5,), (0.5,))])

# 下载数据并分训练集和验证集
train_dataset = torchvision.datasets.MNIST(root='./data/torchvision/MNIST/training', train=True, download=True, transform=MNIST_preprocess)
train_set, val_set = torch.utils.data.random_split(train_dataset, [50000, 10000])

# 下载测试集数据
test_set = torchvision.datasets.MNIST(root='./data/torchvision/MNIST/testing', train=False, download=True, transform=MNIST_preprocess)

# 把数据转换为loader类型
train_loader = torch.utils.data.DataLoader(dataset=train_set, batch_size=32, shuffle=True)
val_loader = torch.utils.data.DataLoader(dataset=val_set, batch_size=32, shuffle=False)
test_loader = torch.utils.data.DataLoader(dataset=test_set, batch_size=32, shuffle=False)

# 打印图像的维度保证他们的一致性
def print_dim(loader, text):
  print('---------'+text+'---------')
  for image, label in loader:
    print(image.shape)
    print(label.shape)
    break

print_dim(train_loader,'source_loader')
print_dim(val_loader,'source_eval_loader')
print_dim(test_loader,'target_loader')

def plot_images(dataset, text):
  NUM_IMAGES = 4
  examples = torch.stack([dataset[idx][0] for idx in range(NUM_IMAGES)], dim=0)
  img_grid = torchvision.utils.make_grid(examples, nrow=2, normalize=True, pad_value=0.9)
  img_grid = img_grid.permute(1, 2, 0)

  plt.figure(figsize=(8, 8))
  plt.title("Image examples from "+text)
  plt.imshow(img_grid)
  plt.axis("off")
  plt.show()
  plt.close()

plot_images(train_loader.dataset,'train_loader')
plot_images(val_loader.dataset,'val_loader')
plot_images(test_loader.dataset,'test_loader')


image_size = 28
embed_dim=256
hidden_dim=embed_dim*3
num_heads=8
num_layers=6
patch_size=7
num_patches=16
num_channels=1
num_classes=10
dropout=0.2


def img_to_patch(x, patch_size, flatten_channels=True):
    """
    Inputs:
        x - Tensor representing the image of shape [B, C, H, W]
        patch_size - Number of pixels per dimension of the patches (integer)
        flatten_channels - If True, the patches will be returned in a flattened format
                           as a feature vector instead of a image grid.
    """
    B, C, H, W = x.shape
    #这一步的目的是将图像分成 H // patch_size 行和 W // patch_size 列的块
    x = x.reshape(B, C, H // patch_size, patch_size, W // patch_size, patch_size)
    #操作对维度进行重新排列
    x = x.permute(0, 2, 4, 1, 3, 5)  # [B, H', W', C, p_H, p_W]
    # 操作将块的第一维 到 第二维合并
    x = x.flatten(1, 2)  # [B, H'*W', C, p_H, p_W]
    if flatten_channels:
        # 操作将块的第二维 到 第四维合并
        x = x.flatten(2, 4)  # [B, H'*W', C*p_H*p_W]
    return x


# Visualize the image patches
NUM_IMAGES = 4
train_examples = torch.stack([train_set[idx][0] for idx in range(NUM_IMAGES)], dim=0)
img_patches = img_to_patch(train_examples, patch_size=patch_size, flatten_channels=False)

fig, ax = plt.subplots(train_examples.shape[0], 1, figsize=(14, 12))
fig.suptitle("Images as input sequences of patches")
for i in range(train_examples.shape[0]):
    img_grid = torchvision.utils.make_grid(img_patches[i], nrow=int(image_size / patch_size), normalize=True,
                                           pad_value=0.9)
    img_grid = img_grid.permute(1, 2, 0)
    ax[i].imshow(img_grid)
    ax[i].axis("off")
plt.show()
plt.close()

class AttentionBlock(nn.Module):
    def __init__(self, embed_dim, hidden_dim, num_heads, dropout=0.0):
        """
        Inputs:
            embed_dim - Dimensionality of input and attention feature vectors
            hidden_dim - Dimensionality of hidden layer in feed-forward network
                         (usually 2-4x larger than embed_dim)
            num_heads - Number of heads to use in the Multi-Head Attention block
            dropout - Amount of dropout to apply in the feed-forward network
        """
        super().__init__()

        self.layer_norm_1 = nn.LayerNorm(embed_dim)
        self.attn = nn.MultiheadAttention(embed_dim, num_heads)
        self.layer_norm_2 = nn.LayerNorm(embed_dim)
        self.linear = nn.Sequential(
            nn.Linear(embed_dim, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, embed_dim),
            nn.Dropout(dropout),
        )

    def forward(self, x):
        inp_x = self.layer_norm_1(x)
        x = x + self.attn(inp_x, inp_x, inp_x)[0]
        x = x + self.linear(self.layer_norm_2(x))
        return x

class VisionTransformer(nn.Module):
    def __init__(
        self,
        embed_dim,
        hidden_dim,
        num_channels,
        num_heads,
        num_layers,
        num_classes,
        patch_size,
        num_patches,
        dropout=0.0,
    ):
        """
        Inputs:
            embed_dim - Dimensionality of the input feature vectors to the Transformer
            hidden_dim - Dimensionality of the hidden layer in the feed-forward networks
                         within the Transformer
            num_channels - Number of channels of the input (3 for RGB or 1 for grayscale)
            num_heads - Number of heads to use in the Multi-Head Attention block
            num_layers - Number of layers to use in the Transformer
            num_classes - Number of classes to predict
            patch_size - Number of pixels that the patches have per dimension
            num_patches - Maximum number of patches an image can have
            dropout - Amount of dropout to apply in the feed-forward network and
                      on the input encoding
        """
        super().__init__()

        self.patch_size = patch_size

        # Layers/Networks
        self.input_layer = nn.Linear(num_channels * (patch_size**2), embed_dim)
        self.transformer = nn.Sequential(
            *(AttentionBlock(embed_dim, hidden_dim, num_heads, dropout=dropout) for _ in range(num_layers))
        )
        self.mlp_head = nn.Sequential(nn.LayerNorm(embed_dim), nn.Linear(embed_dim, num_classes))
        self.dropout = nn.Dropout(dropout)

        # Parameters/Embeddings
        self.cls_token = nn.Parameter(torch.randn(1, 1, embed_dim))
        self.pos_embedding = nn.Parameter(torch.randn(1, 1 + num_patches, embed_dim))

    def forward(self, x):
        # Preprocess input
        x = img_to_patch(x, self.patch_size)        # x.shape ---> batch, num_patches, (patch_size**2)
        B, T, _ = x.shape       #（32,16,49）
        x = self.input_layer(x)                     # x.shape ---> batch, num_patches, embed_dim

        # Add CLS token and positional encoding
        cls_token = self.cls_token.repeat(B, 1, 1)  #cls_token.shape = (32,1,256)
        x = torch.cat([cls_token, x], dim=1)         #x.shape = (32,17,256) x.shape ---> batch, num_patches+1, embed_dim
        x = x + self.pos_embedding[:, : T + 1]      # x.shape ---> batch, num_patches+1, embed_dim

        # Apply Transformer
        x = self.dropout(x)
        x = x.transpose(0, 1)                       # x.shape ---> num_patches+1, batch, embed_dim
        x = self.transformer(x)                     # x.shape ---> num_patches+1, batch, embed_dim

        # Perform classification prediction
        cls = x[0]
        out = self.mlp_head(cls)
        return out

model = VisionTransformer(embed_dim=embed_dim,
                          hidden_dim=hidden_dim,
                          num_heads=num_heads,
                          num_layers=num_layers,
                          patch_size=patch_size,
                          num_channels=num_channels,
                          num_patches=num_patches,
                          num_classes=num_classes,
                          dropout=dropout)

# Transfer to GPU
model.to(device)
model_restore = None #'/content/model_20230712_211204_0'
if model_restore is not None and os.path.exists(model_restore):
  model.load_state_dict(torch.load(model_restore))
  model.restored = True


# setup the loss function
loss_fn = torch.nn.CrossEntropyLoss()
# setup the optimizer with the learning rate
model_optimizer = optim.Adam(model.parameters(), lr=3e-4)
# set a scheduler to decay the learning rate by 0.1 on the 100th 150th epochs
model_scheduler = optim.lr_scheduler.MultiStepLR(model_optimizer,
                                            milestones=[100, 150], gamma=0.1)

# set an empty list to plot the loss later
lossi = []
# set an initial high value for the validation loss
best_vloss = 1_000_000
# set the timestamp to save the training model
timestamp = datetime.now().strftime('%Y%m%d_%H:%M:%S')
# Training loop
for epoch in range(20):
  for imgs, labels in tqdm_notebook(train_loader, desc='epoch '+str(epoch)):
    # Make sure gradient tracking is on, and do a pass over the data
    model.train(True)
    # Transfer to GPU
    imgs, labels = imgs.to(device), labels.to(device)
    # zero the parameter gradients
    model_optimizer.zero_grad()
    # Make predictions for this batch
    preds = model(imgs)
    # Compute the loss and its gradients
    loss = loss_fn(preds, labels)
    # append this loss to the list for later plotting
    lossi.append(loss.item())
    # backpropagate the loss
    loss.backward()
    # adjust parameters based on the calculated gradients
    model_optimizer.step()

  # step the scheduler for the learning rate decay
  model_scheduler.step()
  running_vloss = 0.0
  # Set the model to evaluation mode, disabling dropout and using population
  # statistics for batch normalization.
  model.eval()

  # Disable gradient computation and reduce memory consumption.
  with torch.no_grad():
      for i, vdata in enumerate(val_loader):
          vinputs, vlabels = vdata
          vinputs, vlabels = vinputs.to(device), vlabels.to(device)
          voutputs = model(vinputs)
          vloss = loss_fn(voutputs, vlabels)
          running_vloss += vloss

  avg_vloss = running_vloss / (i + 1)
  print('LOSS train {:.4f} valid {:.4f}'.format(loss.item(), avg_vloss.item()))

  # Track best performance, and save the model's state
  if avg_vloss < best_vloss:
      best_vloss = avg_vloss
      model_path = './model_VisionTransformer_MNIST_{}.pt'.format(epoch+1)
      torch.save(model.state_dict(), model_path)

# plot the training loss by averaging every 3 steps
fig = plt.figure()
ax = fig.add_subplot(111)
# plot the average loss
plt.plot(torch.tensor(lossi).view(-1, 3).mean(1))
plt.title('Training loss')

# Set the model to evaluation mode, disabling dropout.
model.eval()
# evaluate network
acc_total = 0
with torch.no_grad():
  for imgs, labels in tqdm_notebook(test_loader):

    imgs, labels = imgs.to(device), labels.to(device)
    preds = model(imgs)
    pred_cls = preds.data.max(1)[1]
    acc_total += pred_cls.eq(labels.data).cpu().sum()

acc = acc_total.item()/len(test_loader.dataset)
print('Accuracy on test set = '+str(acc))

(test_set.targets==8).nonzero(as_tuple=True)[0][5]

# pull out two test samples
img_tensor_k = test_set.data[0].to(device)
img_tensor_q = test_set.data[146].to(device)
# convert the test sample into patches
patches_k = img_to_patch(img_tensor_k.unsqueeze(0).unsqueeze(0), patch_size=patch_size)
patches_q = img_to_patch(img_tensor_q.unsqueeze(0).unsqueeze(0), patch_size=patch_size)
# run the patches through the input layer to get a tensor of size embed_dim
patches_k_encoded = model.input_layer(patches_k.float())
patches_q_encoded = model.input_layer(patches_q.float())
q = patches_q_encoded
k = patches_k_encoded
att = q @ k.transpose(-2, -1)
print(q.shape)
print(k.shape)
print(att.squeeze().shape)

patches_q_plot = patches_q.reshape(num_patches,num_channels,patch_size,patch_size).detach().cpu().numpy()
patches_k_plot = patches_k.reshape(num_patches,num_channels,patch_size,patch_size).detach().cpu().numpy()


fig = plt.figure(figsize=(6,6))
img = np.asarray(img_tensor_k.cpu())
plt.imshow(img, cmap='gray')
plt.title('key image')
plt.tick_params(axis='both', which='both', bottom=False, top=False, left=False, right=False,
               labelbottom=False, labeltop=False, labelleft=False, labelright=False)
plt.savefig('key.png', bbox_inches='tight', dpi=600)

fig = plt.figure(figsize=(6,6))
img = np.asarray(img_tensor_q.cpu())
plt.imshow(img, cmap='gray')
plt.title('query image')
plt.tick_params(axis='both', which='both', bottom=False, top=False, left=False, right=False,
               labelbottom=False, labeltop=False, labelleft=False, labelright=False)
plt.savefig('query.png', bbox_inches='tight', dpi=600)


fig = plt.figure(figsize=(6,6))
gs = gridspec.GridSpec(17, 17, figure=fig)
ax3 = plt.subplot(gs[:-1, 1:])
ax3.matshow(att.detach().squeeze().cpu().numpy())
ax3.tick_params(axis='both', which='both', bottom=False, top=False, left=False, right=False,
                labelbottom=False, labeltop=False, labelleft=False, labelright=False)

for i in range(num_patches):
    ax = plt.subplot(gs[i, 0])
    ax.imshow(patches_q_plot[i,0,:,:], cmap='gray')
    ax.tick_params(axis='both', which='both', bottom=False, top=False, left=False, right=False,
                labelbottom=False, labeltop=False, labelleft=False, labelright=False)

for i in range(num_patches):
    ax = plt.subplot(gs[-1, i+1])
    ax.imshow(patches_k_plot[i,0,:,:], cmap='gray')
    ax.tick_params(axis='both', which='both', bottom=False, top=False, left=False, right=False,
                labelbottom=False, labeltop=False, labelleft=False, labelright=False)

plt.savefig('att.png', bbox_inches='tight', dpi=600)

fig = plt.figure(figsize=(16.9,5))
gs0 = gridspec.GridSpec(1, 3, figure=fig)
gs00 = gridspec.GridSpecFromSubplotSpec(17, 17, subplot_spec=gs0[0,2])

ax1 = fig.add_subplot(gs0[0, 0])
img = np.asarray(img_tensor_k.cpu())
ax1.imshow(img, cmap='gray')
ax1.set_title('key image')
ax1.tick_params(axis='both', which='both', bottom=False, top=False, left=False, right=False,
               labelbottom=False, labeltop=False, labelleft=False, labelright=False)

ax2 = fig.add_subplot(gs0[0, 1])
img = np.asarray(img_tensor_q.cpu())
ax2.imshow(img, cmap='gray')
ax2.set_title('query image')
ax2.tick_params(axis='both', which='both', bottom=False, top=False, left=False, right=False,
               labelbottom=False, labeltop=False, labelleft=False, labelright=False)

ax3 = plt.subplot(gs00[:-1, 1:])
ax3.matshow(att.detach().squeeze().cpu().numpy())
ax3.tick_params(axis='both', which='both', bottom=False, top=False, left=False, right=False,
                labelbottom=False, labeltop=False, labelleft=False, labelright=False)

for i in range(num_patches):
    ax = plt.subplot(gs00[i, 0])
    ax.imshow(patches_q_plot[i,0,:,:], cmap='gray')
    ax.tick_params(axis='both', which='both', bottom=False, top=False, left=False, right=False,
                labelbottom=False, labeltop=False, labelleft=False, labelright=False)

for i in range(num_patches):
    ax = plt.subplot(gs00[-1, i+1])
    ax.imshow(patches_k_plot[i,0,:,:], cmap='gray')
    ax.tick_params(axis='both', which='both', bottom=False, top=False, left=False, right=False,
                labelbottom=False, labeltop=False, labelleft=False, labelright=False)