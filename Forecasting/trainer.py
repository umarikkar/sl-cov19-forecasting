# -*- coding: utf-8 -*-
"""Trainer.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1WvJQhGKDZBjyiTn3f2Z6tbs0pldmXiDO
"""

# from google.colab import drive
# drive.mount('/content/drive')
# import sys
# import os
# path = "/content/drive/Shareddrives/covid.eng.pdn.ac.lk drive/COVID-AI (PG)/spatio_temporal/sl-cov19-forecasting/Forecasting"
# os.chdir(path)
# sys.path.insert(0, os.path.join(sys.path[0], '..'))
import argparse
import os
import sys
import time
import matplotlib as mpl

mpl.use('Agg')

sys.path.insert(0, os.path.join(sys.path[0], '..'))
import pandas as pd  # Basic library for all of our dataset operations
import numpy as np
import tensorflow as tf
from tensorflow.keras.callbacks import TensorBoard

# plots

import matplotlib.pyplot as plt
from utils.plots import plot_prediction
from utils.functions import normalize_for_nn, undo_normalization, bs
from utils.data_loader import load_data, per_million, get_data, reduce_regions_to_batch, expand_dims, \
    load_multiple_data, load_samples
from utils.smoothing_functions import O_LPF
from utils.data_splitter import split_on_time_dimension, split_into_pieces_inorder, \
    split_and_smooth
from utils.undersampling import undersample3
from models import get_model

# Extra settings
seed = 42
tf.random.set_seed(seed)
np.random.seed(seed)
plt.style.use('bmh')
mpl.rcParams['axes.labelsize'] = 14
mpl.rcParams['xtick.labelsize'] = 12
mpl.rcParams['text.color'] = 'k'
mpl.rcParams['figure.figsize'] = 18, 8

print(tf.__version__)

gpus = tf.config.list_physical_devices('GPU')
if gpus:
    try:
        # Currently, memory growth needs to be the same across GPUs
        for gpu in gpus:
            tf.config.experimental.set_memory_growth(gpu, True)
        logical_gpus = tf.config.experimental.list_logical_devices('GPU')
        print(len(gpus), "Physical GPUs,", len(logical_gpus), "Logical GPUs")
    except RuntimeError as e:
        # Memory growth must be set before GPUs have been initialized
        print(e)


def get_loss_f(undersampling, xcheck, freq):
    def loss_f_normal(y_true, y_pred, x):
        y_pred = tf.dtypes.cast(y_pred, tf.float64)
        return tf.reduce_mean((y_true - y_pred) ** 2)

    def loss_f_new(y_true, y_pred, x):
        region_sample_freq = np.zeros((x.shape[0], x.shape[-1]), dtype='double')

        for batch in range(x.shape[0]):
            for n in range(x.shape[-1]):
                i = bs(xcheck, np.mean(x[batch, :, n])) - 1
                region_sample_freq[batch, n] = freq[i]
        y_pred = tf.dtypes.cast(y_pred, tf.float64)
        mse = tf.reduce_mean((y_true - y_pred) ** 2, 1)

        return tf.reduce_mean(mse * (1 / np.log(region_sample_freq)) ** 2 * 10)

    if undersampling == "Reduce" or undersampling == 'None':
        return loss_f_normal
    else:
        return loss_f_new


def eval_metric(y_true, y_pred):
    return np.mean((np.squeeze(y_true) - np.squeeze(y_pred)) ** 2) ** 0.5


def train(model, train_data, X_train, Y_train, X_test, Y_test):
    print("Model Input shape", model.input.shape)
    print("Model Output shape", model.output.shape)

    tensorboard = TensorBoard(log_dir='./logs/' + folder, write_graph=True, histogram_freq=1, write_images=True)
    tensorboard.set_model(model)

    opt = tf.keras.optimizers.Adam(lr=lr)
    loss_f = get_loss_f(UNDERSAMPLING, xcheck, freq)

    train_metric = []
    val_metric = []
    test_metric = []
    best_test_value = 1e10
    for epoch in range(EPOCHS):
        losses = []
        for x, y in train_data:
            with tf.GradientTape() as tape:
                y_pred = model(x, training=True)
                loss = loss_f(y, y_pred, x)

            grad = tape.gradient(loss, model.trainable_variables)
            opt.apply_gradients(zip(grad, model.trainable_variables))
            losses.append(loss)

            print(f"\r Epoch {epoch}: mean loss = {np.mean(losses):.5f}", end='')
        # add metric value of the prediction (from training data)
        pred_train_y = model(X_train, training=False)
        train_metric.append(eval_metric(Y_train, pred_train_y))
        # add metric value of the prediction (from testing data)
        pred_test_y = model(X_test, training=False)
        test_metric.append(eval_metric(Y_test, pred_test_y))

        if test_metric[-1] < best_test_value:
            best_test_value = test_metric[-1]
            print(f" Best test metric {best_test_value:.5f}. Saving model...")
            model.save("temp.h5")
        if PLOT:
            test1(model, x_data_scalers, str(epoch))
            plt.clf()

            plt.figure(16, figsize=(10, 3))
            plt.plot(train_metric, label='Train')
            plt.plot(test_metric, label='Test')
            plt.xlabel("Epoch")
            plt.ylabel("Metric")
            plt.legend()
            plt.savefig(f"./logs/{folder}/images/Train_metric.png", bbox_inches='tight')
            plt.clf()

    model = tf.keras.models.load_model("temp.h5")
    model.save("models/" + fmodel_name + ".h5")


def main():
    # ============================================================================================ Initialize parameters
    parser = argparse.ArgumentParser(description='Train NN model for forecasting COVID-19 pandemic')
    parser.add_argument('--daily', help='Use daily data', action='store_true')
    parser.add_argument('--dataset', help='Dataset used for training. (SL, Texas, USA, Global)', type=str,
                        nargs='+', default='JP')
    parser.add_argument('--split_date', help='Train-Test splitting date', type=str, default='2021-02-01')

    parser.add_argument('--epochs', help='Epochs to be trained', type=int, default=50)
    parser.add_argument('--batchsize', help='Batch size', type=int, default=16)
    parser.add_argument('--input_days', help='Number of days input into the NN', type=int, default=25)
    parser.add_argument('--output_days', help='Number of days predicted by the model', type=int, default=10)
    parser.add_argument('--modeltype', help='Model type', type=str, default='LSTM_Simple_WO_Regions')

    parser.add_argument('--lr', help='Learning rate', type=float, default=0.002)
    parser.add_argument('--preprocessing', help='Preprocessing on the training data (Unfiltered, Filtered)', type=str,
                        default="Filtered")
    parser.add_argument('--undersampling', help='under-sampling method (None, Loss, Reduce)', type=str,
                        default="Reduce")

    parser.add_argument('--path', help='default dataset path', type=str, default="../Datasets")

    args = parser.parse_args()

    global daily_data, DATASET, DATASETS, split_date, EPOCHS, BATCH_SIZE, BUFFER_SIZE, WINDOW_LENGTH, PREDICT_STEPS, lr, \
        TRAINING_DATA_TYPE, UNDERSAMPLING, PLOT, daily_cases, daily_filtered, population, region_names, test_days, \
        x_data_scalers, folder, fmodel_name, count_h, count_l, num_l, num_h, power_l, power_h, power_penalty, clip_percentages
    daily_data = args.daily
    DATASETS = args.dataset
    if len(DATASETS) == 1:
        DATASETS = DATASETS[0].split(' ')
    split_date = args.split_date

    EPOCHS = args.epochs
    BATCH_SIZE = args.batchsize
    BUFFER_SIZE = 100
    WINDOW_LENGTH = args.input_days
    PREDICT_STEPS = args.output_days
    lr = args.lr
    TRAINING_DATA_TYPE = args.preprocessing
    UNDERSAMPLING = args.undersampling

    midpoint = True

    if midpoint:
        R_EIG_ratio = 1.02
        R_power = 1
    else:
        R_EIG_ratio = 3
        R_power = 1

    look_back_window, window_slide = 100, 20
    PLOT = True

    # ===================================================================================================== Loading data

    """Required variables:

    *   **region_names** - Names of the unique regions.
    *   **confirmed_cases** - 2D array. Each row should corresponds to values in 'region_names'. 
                            Each column represents a day. Columns should be in ascending order. 
                            (Starting day -> Present)
    *   **daily_cases** - confirmed_cases.diff()
    *   **population** - Population in 'region'
    *   **features** - Features of the regions. Each column is a certain feature.
    *   **START_DATE** - Starting date of the data DD/MM/YYYY
    *   **n_regions** Number of regions


    """
    DATASET = "SL"
    d = load_data(DATASET, path=args.path)
    region_names = d["region_names"]
    confirmed_cases = d["confirmed_cases"]
    daily_cases = d["daily_cases"]
    features = d["features"]
    START_DATE = d["START_DATE"]
    n_regions = d["n_regions"]
    daily_cases[daily_cases < 0] = 0
    population = features["Population"]
    for i in range(len(population)):
        print("{:.2f}%".format(confirmed_cases[i, :].max() / population[i] * 100), region_names[i])

    days = confirmed_cases.shape[1]
    n_features = features.shape[1]

    test_days = 100

    print(f"Total population {population.sum() / 1e6:.2f}M, regions:{n_regions}, days:{days}")

    # df = pd.DataFrame(daily_cases.T, columns=features.index)
    # df.index = pd.to_datetime(pd.to_datetime(START_DATE).value + df.index * 24 * 3600 * 1000000000)

    features = features.values

    daily_filtered, cutoff_freqs = O_LPF(daily_cases, datatype='daily', order=3, midpoint=midpoint, corr=True,
                                         R_EIG_ratio=R_EIG_ratio, R_power=R_power,
                                         region_names=region_names, plot_freq=1, view=False)

    # ================================================================================================= Initialize Model

    model, reduce_regions2batch = get_model(args.modeltype,
                                            input_days=WINDOW_LENGTH,
                                            output_days=PREDICT_STEPS,
                                            n_features=n_features,
                                            n_regions=n_regions)

    fmodel_name = str(DATASETS) + "_" + model.name + "_" + TRAINING_DATA_TYPE + '_' + UNDERSAMPLING + '_' + str(
        model.input.shape[1]) + '_' + str(model.output.shape[1])

    print(fmodel_name)
    folder = time.strftime('%Y.%m.%d-%H.%M.%S', time.localtime()) + "_" + fmodel_name
    os.makedirs('./logs/' + folder + '/images')
    # ===================================================================================== Preparing data for training
    if PLOT:
        plt.plot(daily_cases.T)
        plt.savefig('./logs/' + folder + "/images/raw_data.png", bbox_inches='tight')
        plt.plot(daily_filtered.T)
        plt.savefig('./logs/' + folder + "/images/filtered_data.png", bbox_inches='tight')

    x_data, y_data, x_data_scalers = get_data(False, normalize=True, data=daily_cases, dataf=daily_filtered,
                                              population=population)
    x_dataf, y_dataf, x_data_scalersf = get_data(True, normalize=True, data=daily_cases, dataf=daily_filtered,
                                                 population=population)

    fil, raw, fs = load_multiple_data(DATASETS, args.path, look_back_window, window_slide, R_EIG_ratio, R_power,
                                      midpoint)
    for i_region in range(len(fil)):
        if fil[i_region].shape[0] < test_days:
            Warning(f"Region has too few data {fil[i_region].shape[0]} to train, can't keep {test_days} samples as test data.")
        else:
            print(f"Total samples for {i_region} is {len(fil[i_region])}. Dropping last {test_days}")
            fil[i_region] = fil[i_region][:-test_days]
            raw[i_region] = raw[i_region][:-test_days]

    if TRAINING_DATA_TYPE == "Filtered":
        temp = load_samples(fil, fs, WINDOW_LENGTH, PREDICT_STEPS)
        x_train_list, y_train_list, x_test_list, y_test_list, x_val_list, y_val_list, fs_train, fs_test, fs_val = temp
    else:
        temp = load_samples(raw, fs, WINDOW_LENGTH, PREDICT_STEPS)
        x_train_list, y_train_list, x_test_list, y_test_list, x_val_list, y_val_list, fs_train, fs_test, fs_val = temp

    # ==================================================================================================== Undersampling
    print("================================================== Training data before undersampling")
    total_regions, total_samples = 0, 0
    for i in range(len(x_train_list)):  # (n_regions, samples*, WINDOW_LENGTH)
        total_regions += 1
        total_samples += x_train_list[i].shape[0]
    for i in range(len(x_test_list)):  # (n_regions, samples*, WINDOW_LENGTH)
        total_regions += 1
        total_samples += x_test_list[i].shape[0]
    for i in range(len(x_val_list)):  # (n_regions, samples*, WINDOW_LENGTH)
        total_regions += 1
        total_samples += x_val_list[i].shape[0]
    print(f"Total regions {total_regions} Total samples {total_samples}")

    if UNDERSAMPLING == "Reduce":
        # under-sampling parameters

        optimised = True
        clip = True

        if optimised:
            if clip:
                clip_percentages = [0, 10]
            count_h, count_l, num_h, num_l = 2, 0.2, 10000, 100
            power_l, power_h, power_penalty = 0.2, 2, 1000
        else:
            ratio = 0.3

        x_train_list, y_train_list, fs_train = undersample3(x_train_list, y_train_list, fs_train, count_h, count_l,
                                                            num_h, num_l, power_l, power_h, power_penalty, clip,
                                                            clip_percentages, str(DATASETS), PLOT,
                                                            f'./logs/{folder}/images/under_{DATASETS}.png' if PLOT else None)

        print(f"Undersample percentage {x_train_list[0].shape[0] / total_samples * 100:.2f}%")
        # EPOCHS = min(250, int(EPOCHS * total_samples / x_train_list[0].shape[0]))
        print(f"New Epoch = {EPOCHS}")
        # here Xtrain have been reduced by regions

    print("================================================= Training data after undersampling")
    # print("Train", x_train.shape, y_train.shape, x_train_feat.shape)
    # print("Val", x_val.shape, y_val.shape, x_val_feat.shape)
    # print("Test", x_test.shape, y_test.shape, x_test_feat.shape)
    if reduce_regions2batch:
        x_train_list, y_train_list, fs_train = reduce_regions_to_batch([x_train_list, y_train_list, fs_train])
        x_test_list, y_test_list, fs_test = reduce_regions_to_batch([x_test_list, y_test_list, fs_test])
        x_val_list, y_val_list, fs_val = reduce_regions_to_batch([x_val_list, y_val_list, fs_val])

        x_train, y_train, x_train_feat = expand_dims([x_train_list, y_train_list, fs_train], 3)
        x_test, y_test, x_test_feat = expand_dims([x_test_list, y_test_list, fs_test], 3)
        x_val, y_val, x_val_feat = expand_dims([x_val_list, y_val_list, fs_val], 3)
    else:
        raise NotImplementedError()

    # ============================================================================================== Creating Dataset
    train_data = tf.data.Dataset.from_tensor_slices((x_train, y_train))
    train_data = train_data.cache().shuffle(BUFFER_SIZE).batch(BATCH_SIZE)

    global freq, xcheck
    freq, xcheck = np.histogram(np.concatenate(x_train, -1).mean(0))

    print("================================================== Training data after reducing shapes")
    print("Train", x_train.shape, y_train.shape, x_train_feat.shape)
    print("Val", x_val.shape, y_val.shape, x_val_feat.shape)
    print("Test", x_test.shape, y_test.shape, x_test_feat.shape)

    if PLOT:
        fig, axs = plt.subplots(2, 2)
        x_data, y_data, _ = get_data(filtered=False, normalize=True, data=daily_cases, dataf=daily_filtered,
                                     population=population)
        axs[0, 0].plot(x_data)
        axs[0, 0].set_title("Original data")

        for i in range(x_train.shape[-1]):
            idx = np.random.randint(0, len(x_train), 100)
            axs[0, 1].plot(np.concatenate([x_train[idx, :, i], y_train[idx, :, i]], 1).T, linewidth=1)
        axs[0, 1].axvline(x_train.shape[1], color='r', linestyle='--')

        axs[1, 0].hist(x_train.reshape(-1), bins=100)
        axs[1, 0].set_title("Histogram of cases")

        axs[1, 1].hist(np.concatenate(x_train, -1).mean(0), bins=100)
        axs[1, 1].set_title("Histogram of mean of training samples")

        plt.savefig('./logs/' + folder + f"/images/Train_data.png", bbox_inches='tight')

    # =================================================================================================  Train

    train(model, train_data, x_train, y_train, x_test, y_test)

    # ================================================================================================= Few Evaluations

    if PLOT:
        test1(model, x_data_scalers, "Final")
        test2(model, x_data_scalers)
        test_evolution(model)


def test1(model, x_data_scalers, epoch):
    n_regions = len(x_data_scalers.data_max_)

    def get_model_predictions(model, x_data, y_data, scalers):
        print(
            f"Predicting from model (in:{model.input.shape} out:{model.output.shape}). X={x_data.shape} Y={y_data.shape}")
        # CREATING TRAIN-TEST SETS FOR CASES
        x_test, y_test = split_into_pieces_inorder(x_data.T, y_data.T, WINDOW_LENGTH, PREDICT_STEPS,
                                                   WINDOW_LENGTH + PREDICT_STEPS,
                                                   reduce_last_dim=False)

        if model.input.shape[-1] == 1:
            y_pred = np.zeros_like(y_test)
            for i in range(len(region_names)):
                y_pred[:, :, i] = model(x_test[:, :, i:i + 1])[:, :, 0]
        else:
            y_pred = model(x_test).numpy()

        # # NOTE:
        # # max value may change with time. then we have to retrain the model!!!!!!
        # # we can have a predefined max value. 1 for major cities and 1 for smaller districts
        x_test = undo_normalization(x_test, scalers)
        y_test = undo_normalization(y_test, scalers)
        y_pred = undo_normalization(y_pred, scalers)

        return x_test, y_test, y_pred

    x_data, y_data, _ = get_data(filtered=False, normalize=x_data_scalers, data=daily_cases, dataf=daily_filtered,
                                 population=population)
    x_test, y_test, y_pred = get_model_predictions(model, x_data, y_data, x_data_scalers)
    x_data, y_data, _ = get_data(filtered=True, normalize=x_data_scalers, data=daily_cases, dataf=daily_filtered,
                                 population=population)
    x_testf, y_testf, y_predf = get_model_predictions(model, x_data, y_data, x_data_scalers)

    Ys = np.stack([y_test, y_testf, y_pred, y_predf], 1)
    method_list = ['Observations Raw',
                   'Observations Filtered',
                   'Predicted using raw data',
                   'Predicted using Filtered data']
    styles = {
        'X': {'Preprocessing': 'Raw', 'Data': 'Training', 'Size': 2},
        'Xf': {'Preprocessing': 'Filtered', 'Data': 'Training', 'Size': 2},
        'Observations Raw': {'Preprocessing': 'Raw', 'Data': 'Training', 'Size': 2},
        'Observations Filtered': {'Preprocessing': 'Filtered', 'Data': 'Training', 'Size': 2},
        'Predicted using raw data': {'Preprocessing': 'Raw', 'Data': 'Predicted using raw data', 'Size': 4},
        'Predicted using Filtered data': {'Preprocessing': 'Filtered', 'Data': 'Predicted using Filtered data',
                                          'Size': 3},

    }
    # x_data, y_data = get_data(filtered=False, normalize=False)
    # region_mask = (np.mean(x_data,0) > 50).astype('int32')
    region_mask = (np.arange(n_regions) == 4).astype('int32')

    plt.figure(figsize=(20, 10))
    plot_prediction(x_test, x_testf, Ys, method_list, styles, region_names, region_mask)
    plt.title(str(epoch))
    # plt.savefig(f"./logs/{folder}/images/test1_{epoch}.eps")
    plt.savefig(f"./logs/{folder}/images/test1_{epoch}.png")


def test2(model, x_data_scalers):
    n_regions = len(x_data_scalers.data_max_)

    def get_model_predictions(model, x_data, y_data, scalers):
        print(f"Predicting from model. X={x_data.shape} Y={y_data.shape}")
        X_w = []
        y_w = []
        for i in range(WINDOW_LENGTH - 1, len(x_data)):
            X_w.append(x_data[i - WINDOW_LENGTH + 1:i + 1])
            y_w.append(y_data[i])
        X_w, y_w = np.array(X_w), np.array(y_w)

        X_test_w = X_w[-test_days - WINDOW_LENGTH:-1]
        y_test_w = y_w[-test_days - WINDOW_LENGTH:-1]

        if model.input.shape[-1] == 1:
            yhat = []
            for col in range(n_regions):
                yhat.append(model.predict(X_test_w[:, :, col:col + 1])[:, 0].reshape(1, -1)[0])
            yhat = np.squeeze(np.array(yhat)).T
        else:
            yhat = model.predict(X_test_w[:, :, :])[:, 0].reshape(-1, n_regions)

        yhat = undo_normalization(yhat, scalers)[0]
        y_test_w = undo_normalization(y_test_w, scalers)[0]
        return X_test_w, y_test_w, yhat

    x_data, y_data, _ = get_data(filtered=False, normalize=x_data_scalers, data=daily_cases, dataf=daily_filtered,
                                 population=population)
    _, y_test, yhat = get_model_predictions(model, x_data, y_data, x_data_scalers)
    x_dataf, y_dataf, _ = get_data(filtered=True, normalize=x_data_scalers, data=daily_cases, dataf=daily_filtered,
                                   population=population)
    _, y_test, yhatf = get_model_predictions(model, x_dataf, y_dataf, x_data_scalers)

    x_data, y_data, _ = get_data(filtered=False, normalize=False, data=daily_cases, dataf=daily_filtered,
                                 population=population)
    x_dataf, y_dataf, _ = get_data(filtered=True, normalize=False, data=daily_cases, dataf=daily_filtered,
                                   population=population)
    X = np.expand_dims(x_data[:-test_days, :], 0)
    Xf = np.expand_dims(x_dataf[:-test_days, :], 0)
    Y = y_data[-test_days:, :]
    Yf = y_dataf[-test_days:, :]

    Ys = [Y, Yf, yhat, yhatf]
    method_list = ['Observations Raw',
                   'Observations Filtered',
                   f'Predictions using Raw data (Model {TRAINING_DATA_TYPE} {DATASET} data)',
                   f'Predictions using Filtered data (Model {TRAINING_DATA_TYPE} {DATASET} data)',
                   ]
    styles = {
        'X': {'Preprocessing': 'Raw', 'Data': 'Training', 'Size': 2},
        'Xf': {'Preprocessing': 'Filtered', 'Data': 'Training', 'Size': 2},
        'Observations Raw': {'Preprocessing': 'Raw', 'Data': 'Training', 'Size': 2},
        'Observations Filtered': {'Preprocessing': 'Filtered', 'Data': 'Training', 'Size': 2},
        f'Predictions using Raw data (Model {TRAINING_DATA_TYPE} {DATASET} data)': {'Preprocessing': 'Raw',
                                                                                    'Data': f'Predictions using Raw data (Model {TRAINING_DATA_TYPE} {DATASET} data)',
                                                                                    'Size': 4},
        f'Predictions using Filtered data (Model {TRAINING_DATA_TYPE} {DATASET} data)': {'Preprocessing': 'Filtered',
                                                                                         'Data': f'Predictions using Filtered data (Model {TRAINING_DATA_TYPE} {DATASET} data)',
                                                                                         'Size': 3},

    }
    for i in range(len(Ys)):
        print(method_list[i], Ys[i].shape)
        Ys[i] = np.expand_dims(Ys[i], 0)
    Ys = np.stack(Ys, 1)

    # region_mask = ((200 > np.mean(x_data,0)) * (np.mean(x_data,0) > 00)).astype('int32')
    region_mask = (np.arange(n_regions) == 4).astype('int32')

    plt.figure(figsize=(18, 9))

    plot_prediction(X, Xf, Ys, method_list, styles, region_names, region_mask)

    # plt.savefig(f"./logs/{folder}/images/test2.eps")
    plt.savefig(f"./logs/{folder}/images/test2.png")


def test_evolution(model):
    x = model.input.shape[-2]
    r = model.input.shape[-1]
    start_seqs = [np.random.random((1, x, r)),
                  np.ones((1, x, r)) * 0,
                  np.ones((1, x, r)) * 0.5,
                  np.ones((1, x, r)) * 1,
                  np.arange(x * r).reshape((1, x, r)) / 30,
                  np.sin(np.arange(x) / x * np.pi / 2).reshape((1, x, 1)).repeat(r, -1)
                  ]

    predictions = []
    for start_seq in start_seqs:
        input_seq = np.copy(start_seq)
        print(input_seq.shape)
        predict_seq = [start_seq[0, :, :]]
        for _ in range(50):
            output = model(input_seq, training=False)

            input_seq = input_seq[:, output.shape[1]:, :]
            if len(output.shape) == 2:
                output = np.expand_dims(output, -1)
            predict_seq.append(output[0])
            input_seq = np.concatenate([input_seq, output], 1)
        predictions.append(np.concatenate(predict_seq, 0))
    plt.figure()
    plt.semilogy(1 + np.array(predictions)[:, :30, 0].T)
    plt.savefig(f"./logs/{folder}/images/test_future.png", bbox_inches='tight')


if __name__ == "__main__":
    main()
