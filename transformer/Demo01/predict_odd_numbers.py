import argparse
from numpy import arange, random
from torch import save, load, no_grad, LongTensor
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, random_split
from number_loader import NumberLoader
from model import TransformerModel


def train(model, criterion, optimizer, loader):
    model.train()
    epoch_loss = 0
    for i, batch in enumerate(loader):
        src, tgt = batch
        src, tgt = src.transpose(1, 0).cuda(), tgt.transpose(1, 0).cuda()
        optimizer.zero_grad()
        output = model(src, tgt[:-1, :])
        n = output.shape[-1]
        loss = criterion(output.reshape(-1, n), tgt[1:, :].reshape(-1))
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0)   #用于裁剪模型的梯度，以避免梯度爆炸的问题
        optimizer.step()    #执行优化器的一步操作，用于更新模型的参数
        epoch_loss += loss.item()
    return epoch_loss / len(loader)


def validation(model, criterion, loader):
    model.eval()
    epoch_loss = 0
    with no_grad():
        for i, batch in enumerate(loader):
            src, tgt = batch
            src, tgt = src.transpose(1, 0).cuda(), tgt.transpose(1, 0).cuda()
            output = model(src, tgt[:-1, :])
            n = output.shape[-1]
            loss = criterion(output.reshape(-1, n), tgt[1:, :].reshape(-1))
            epoch_loss += loss.item()
    return epoch_loss / len(loader)


def test(model, max_len=3, test_times=1):
    model = model.cuda()
    model.eval()
    with no_grad():
        for i in range(test_times):
            s = random.randint(1, 4998)
            cpu_src = [(s + j) * 2 for j in range(max_len)]
            src = LongTensor(cpu_src).unsqueeze(1).cuda()
            tgt = [0] + [(s + j) * 2 + 1 for j in range(max_len)]
            pred = [0]
            for j in range(max_len):
                inp = LongTensor(pred).unsqueeze(1).cuda()
                output = model(src, inp)
                out_num = output.argmax(2)[-1].item()
                pred.append(out_num)
            print("input: ", cpu_src)
            print("target: ", tgt)
            print("predict: ", pred)


def main(model_name=None, hidden=64, nlayers=1):
    voc_size = 10000
    inp = arange(2, voc_size, 2)
    tgt = arange(3, voc_size, 2)
    batch_size = 128
    epochs = 100
    """
        inp是输入
        tgt是目标数值
    """
    dataset = NumberLoader(inp, tgt)
    #9:1分训练和测试的数据
    train_len = int(len(dataset) * 0.9)
    val_len = len(dataset) - train_len
    train_set, val_set = random_split(dataset, [train_len, val_len])
    train_loader = DataLoader(train_set, batch_size=batch_size, shuffle=True, num_workers=1)
    val_loader = DataLoader(val_set, batch_size=batch_size, shuffle=True, num_workers=1)
    model = TransformerModel(voc_size, voc_size, hidden=hidden, nlayers=nlayers)
    if model_name is not None:
        model.load_state_dict(load(model_name))
    model = model.cuda()
    # optimizer = optim.SGD(model.parameters(), lr=0.5)
    optimizer = optim.Adam(model.parameters())
    # scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=10, gamma=0.5)
    criterion = nn.CrossEntropyLoss()
    best_loss = 40
    for i in range(epochs):
        #训练模型
        epoch_loss = train(model, criterion, optimizer, train_loader)
        #验证模型的损失
        epoch_loss_val = validation(model, criterion, val_loader)
        # scheduler.step()
        print("epoch: {} train loss: {}".format(i, epoch_loss))
        print("epoch: {} val loss: {}".format(i, epoch_loss_val))
        if epoch_loss_val < best_loss:
            best_loss = epoch_loss_val
            model_name = "model/model_{0:.5f}.pt".format(epoch_loss_val)
            save(model.state_dict(), model_name)
    return model_name


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='A PyTorch Transformer Language Model for Predicting Odd Numbers')
    parser.add_argument('--test_model', type=str, help='the model file to load')
    parser.add_argument('--train_model', type=str, help='the model file to load')
    args = parser.parse_args()
    '''
        hidden：隐藏
        nlayer：多头注意力机制的层数
    '''
    hidden = 128
    nlayers = 2
    if args.test_model is None:
        if args.train_model is not None:
            model_name = main(args.train_model, hidden=hidden, nlayers=nlayers)
        else:
            model_name = main(hidden=hidden, nlayers=nlayers)
    else:
        model_name = args.test_model
    model = TransformerModel(10000, 10000, hidden=hidden, nlayers=nlayers)
    model.load_state_dict(load(model_name))
    test(model, test_times=10)