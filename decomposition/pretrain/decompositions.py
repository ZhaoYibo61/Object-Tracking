import tensorly as tl
from tensorly.decomposition import parafac, partial_tucker
import numpy as np
import torch
import torch.nn as nn
from VBMF import VBMF

def cp_decomposition_conv_layer(layer, rank):
    """ Gets a conv layer and a target rank, 
        returns a nn.Sequential object with the decomposition """

    # Perform CP decomposition on the layer weight tensor. 
    print(layer, rank)
    X = layer.weight.data.numpy()
    size = max(X.shape)
    # Using the SVD init gives better results, but stalls for large matrices.
    if size >= 256:
        print("Init random")
        last, first, vertical, horizontal = parafac(X, rank=rank, init = 'random')
    else:
        last, first, vertical, horizontal = parafac(X, rank=rank, init = 'svd')

    pointwise_s_to_r_layer = torch.nn.Conv2d(in_channels = first.shape[0], \
            out_channels = first.shape[1],
            kernel_size = 1, \
            stride = layer.stride,
            padding = 0,
            dilation = layer.dilation,
            bias = False)

    depthwise_vertical_layer = torch.nn.Conv2d(in_channels = vertical.shape[1], \
            out_channels = vertical.shape[1],
            kernel_size = (vertical.shape[0], 1),
            stride = layer.stride,
            padding = (layer.padding[0], 0),
            dilation = layer.dilation,
            groups = vertical.shape[1],
            bias = False)

    depthwise_horizontal_layer = torch.nn.Conv2d(in_channels = horizontal.shape[1], \
            out_channels = horizontal.shape[1],
            kernel_size = (1, horizontal.shape[0]),
            stride = layer.stride,
            padding = (0, layer.padding[0]),
            dilation = layer.dilation,
            groups = horizontal.shape[1],
            bias = False)

    pointwise_r_to_t_layer = torch.nn.Conv2d(in_channels = last.shape[1], \
            out_channels = last.shape[0],
            kernel_size = 1, \
            stride = layer.stride,
            padding = 0,
            dilation = layer.dilation,
            bias = True)
    pointwise_r_to_t_layer.bias.data = layer.bias.data

    # Transpose dimensions back to what PyTorch expects
    depthwise_vertical_layer_weights = np.expand_dims(np.expand_dims(\
        vertical.transpose(1, 0), axis = 1), axis = -1)
    depthwise_horizontal_layer_weights = np.expand_dims(np.expand_dims(\
        horizontal.transpose(1, 0), axis = 1), axis = 1)
    pointwise_s_to_r_layer_weights = np.expand_dims(\
        np.expand_dims(first.transpose(1, 0), axis = -1), axis = -1)
    pointwise_r_to_t_layer_weights = np.expand_dims(np.expand_dims(\
        last, axis = -1), axis = -1)

    # Fill in the weights of the new layers
    depthwise_horizontal_layer.weight.data = \
        torch.from_numpy(np.float32(depthwise_horizontal_layer_weights))
    depthwise_vertical_layer.weight.data = \
        torch.from_numpy(np.float32(depthwise_vertical_layer_weights))
    pointwise_s_to_r_layer.weight.data = \
        torch.from_numpy(np.float32(pointwise_s_to_r_layer_weights))
    pointwise_r_to_t_layer.weight.data = \
        torch.from_numpy(np.float32(pointwise_r_to_t_layer_weights))

    new_layers = [pointwise_s_to_r_layer, depthwise_vertical_layer, \
                    depthwise_horizontal_layer, pointwise_r_to_t_layer]
    return nn.Sequential(*new_layers)

def estimate_ranks_tucker(layer):
    """ Unfold the 2 modes of the Tensor the decomposition will 
    be performed on, and estimates the ranks of the matrices using VBMF 
    """

    weights = layer.weight.data.numpy()
    print(type(weights))
    unfold_0 = tl.base.unfold(weights, 0) 
    unfold_1 = tl.base.unfold(weights, 1)
    _, diag_0, _, _ = VBMF.EVBMF(unfold_0)
    _, diag_1, _, _ = VBMF.EVBMF(unfold_1)
    ranks = [diag_0.shape[0], diag_1.shape[1]]
    return ranks

def tucker_decomposition_conv_layer(layer):
    """ Gets a conv layer, 
        returns a nn.Sequential object with the Tucker decomposition.
        The ranks are estimated with a Python implementation of VBMF
        https://github.com/CasvandenBogaard/VBMF
    """

    ranks = estimate_ranks_tucker(layer)
    print(layer, "VBMF Estimated ranks", ranks)
    core, [last, first] = \
        partial_tucker(layer.weight.data.numpy(), \
            modes=[0, 1], ranks=ranks, init='svd')

    # A pointwise convolution that reduces the channels from S to R3
    first_layer = torch.nn.Conv2d(in_channels = first.shape[0], \
            out_channels = first.shape[1],
            kernel_size = 1, \
            stride = 1, # layer.stride,
            padding = 0,
            dilation = layer.dilation,
            bias = False)

    # A regular 2D convolution layer with R3 input channels 
    # and R3 output channels
    core_layer = torch.nn.Conv2d(in_channels = core.shape[1], \
            out_channels = core.shape[0],
            kernel_size = layer.kernel_size,
            stride = layer.stride,
            padding = layer.padding,
            dilation = layer.dilation,
            bias = False)

    # A pointwise convolution that increases the channels from R4 to T
    last_layer = torch.nn.Conv2d(in_channels = last.shape[1], \
            out_channels = last.shape[0],
            kernel_size = 1, \
            stride = 1, # layer.stride,
            padding = 0,
            dilation = layer.dilation,
            bias = True)

    last_layer.bias.data = layer.bias.data


    # Transpose add dimensions to fit into the PyTorch tensors
    first = first.transpose((1, 0))
    first_layer.weight.data = torch.from_numpy(np.float32(\
        np.expand_dims(np.expand_dims(first.copy(), axis=-1), axis=-1)))
    last_layer.weight.data = torch.from_numpy(np.float32(\
        np.expand_dims(np.expand_dims(last.copy(), axis=-1), axis=-1)))
    core_layer.weight.data = torch.from_numpy(np.float32(core.copy()))

    new_layers = [first_layer, core_layer, last_layer]
    return nn.Sequential(*new_layers)

def estimate_ranks_svd(layer):
    """ Unfold the 2 modes of the Tensor the decomposition will 
    be performed on, and estimates the ranks of the matrices using VBMF 
    """

    weights = layer.weight.data.numpy()
    print(type(weights))
    # unfold_0 = tl.base.unfold(weights, 0) 
    # unfold_1 = tl.base.unfold(weights, 1)
    # _, diag_0, _, _ = VBMF.EVBMF(unfold_0)
    # _, diag_1, _, _ = VBMF.EVBMF(unfold_1)
    # ranks = [diag_0.shape[0], diag_1.shape[1]]
    _, diag, _, _ = VBMF.EVBMF(weights)
    ranks = diag.shape[0]
    return ranks

def svd_decomposition_dense_layer(layer):
    """ 
    Gets a fully connected layer,
    returns a nn.Sequential object with the svd decomposition.
    """

    # TODO how to estimate rank using VBMF
    rank = estimate_ranks_svd(layer)

    # Perform SVD decomposition on the layer weight tensor. 
    print(layer, "SVD estimated rank", rank)
    X = layer.weight.data
    size = X.shape
    U, S, V = X.svd()

    # reduce rank
    Uk = U[:, :rank]
    Sk = S[:rank]
    Vk = V[:, :rank]

    # create empty layer
    V_layer = nn.Linear(rank, X.shape[1], bias=False)
    # S_layer = nn.Linear(rank, rank, bias = False)
    U_layer = nn.Linear(X.shape[0], rank, bias=True)

    # putting weight into layer
    V_layer.weight.data = Vk.t()
    # S_layer.weight.data = torch.diag(Sk)
    # U_layer.weight.data = Uk
    U_layer.weight.data = torch.mm(Uk, torch.diag(Sk))
    U_layer.bias.data = layer.bias.data

    # putting layer together
    new_layers = [V_layer, U_layer]
    return nn.Sequential(*new_layers)

    # TODO finished and need testing


       
