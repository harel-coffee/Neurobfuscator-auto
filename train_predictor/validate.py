#!/usr/bin/env python

import time
import argparse
import os
import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3' 
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
# from multiprocessing import cpu_count
import math
import numpy as np
import tensorflow as tf
# print("Num GPUs Available: ", len(tf.config.experimental.list_physical_devices('GPU')))
from data_feeder import DataFeeder, Dataset
from util import infolog, ValueWindow

layer_int_to_name_map = {0:'conv', 1:'fc', 2:'pooling', 3:'bn', 4:'depthConv',5:'relu', 6:'pointConv', 7:'add',8:'softmax'}

log = infolog.log

# Some configs. (latency, r, w, r/w, i/o)

# OP classes + blank + others (10)
# num_classes = 10

# # Hyper-parameters
# num_epochs = 150   #10000
# num_hidden = 96
# num_layers = 1

# batch_size = 1

def convert_decode_to_str(decoded_tensor):
    d = decoded_tensor[0]
    # Replacing blank label to none
    str_decoded = ''
    for x in np.asarray(d[1]):
        if x in layer_int_to_name_map:
            str_decoded = str_decoded + layer_int_to_name_map[x]+ ' '
        else:
            print("x = %d MAJOR ERROR? OUT OF PREDICTION SCOPE" % x)
    return str_decoded

def add_stats(cost, ler):
  with tf.variable_scope('stats') as scope:
    #tf.summary.histogram('linear_outputs', model.linear_outputs)
    #tf.summary.histogram('linear_targets', model.linear_targets)
    tf.summary.scalar('val_cost', cost)  # FIXME: now only feed with val data
    tf.summary.scalar('val_ler', ler)
    #tf.summary.scalar('learning_rate', model.learning_rate)
    return tf.summary.merge_all()


def run_ctc(num_features, log_dir, args):
    which_data = 1
    checkpoint_path = os.path.join(log_dir, 'model.ckpt')
    input_path = args.input
    dataset_path = args.dataset
    normalize = args.normalize
    num_hidden = args.num_hidden
    num_layers = args.num_layers
    num_classes = args.num_classes
    batch_size = args.batch_size
    num_epochs = args.num_epochs
    print(dataset_path)
    log('Checkpoint path: %s' % checkpoint_path)
    log('Loading training data from: %s' % input_path)
    log('Using model: %s' % args.model_path)

    # Set up DataFeeder:
    if which_data == 0:
        dataset = DataFeeder(input_path)
    else:
        dataset = Dataset(dataset_path, normalize)
    # Build the model
    graph = tf.Graph()
    with graph.as_default():
        # tf.debugging.set_log_device_placement(True)
        # e.g: log filter bank or MFCC features
        # Has size [batch_size, max_step_size, num_features], but the
        # batch_size and max_step_size can vary along each step
        inputs = tf.placeholder(tf.float32, [None, None, num_features]) #batch size = 1


        #inputs = tf.placeholder(tf.float32, [None,num_features])
        # Here we use sparse_placeholder that will generate a
        # SparseTensor required by ctc_loss op.
        targets = tf.sparse_placeholder(tf.int32)

        # 1d array of size [batch_size]
        seq_len = tf.placeholder(tf.int32, [None])

        # Defining the cell
        # Can be:
        #   tf.nn.rnn_cell.RNNCell
        #   tf.nn.rnn_cell.GRUCell
        cell = tf.contrib.rnn.LSTMCell(num_hidden, state_is_tuple=True)
        # cell = tf.nn.rnn_cell.BasicRNNCell(num_hidden)
        # cell = tf.nn.rnn_cell.GRUCell(num_units = num_hidden)
        # Stacking rnn cells
        stack = tf.contrib.rnn.MultiRNNCell([cell] * num_layers,
                                            state_is_tuple=True)

        # The second output is the last state and we will no use that
        outputs, _ = tf.nn.dynamic_rnn(stack, inputs, seq_len, dtype=tf.float32)


        #Use some other encoder. give an input, get an output with the same shape

        shape = tf.shape(inputs)
        batch_s, max_time_steps = shape[0], shape[1]

        # Reshaping to apply the same weights over the timesteps
        outputs = tf.reshape(outputs, [-1, num_hidden])

        # Truncated normal with mean 0 and stdev=0.1
        # Tip: Try another initialization
        # see https://www.tensorflow.org/versions/r0.9/api_docs/python/contrib.layers.html#initializers
        W = tf.Variable(tf.truncated_normal([num_hidden,
                                             num_classes],
                                            stddev=0.1))
        # Zero initialization
        # Tip: Is tf.zeros_initializer the same?
        b = tf.Variable(tf.constant(0., shape=[num_classes]))

        # Doing the affine projection
        logits = tf.matmul(outputs, W) + b

        # Reshaping back to the original shape
        logits = tf.reshape(logits, [batch_s, -1, num_classes])

        # Time major
        logits = tf.transpose(logits, (1, 0, 2))

        loss = tf.nn.ctc_loss(targets, logits, seq_len, ctc_merge_repeated = False)
        cost = tf.reduce_mean(loss)

        optimizer = tf.train.AdamOptimizer(learning_rate=0.001).minimize(cost)
        # optimizer = tf.train.MomentumOptimizer(learning_rate=0.01, momentum=0.9).minimize(cost)
        #optimizer = tf.train.MomentumOptimizer(learning_rate=0.005, momentum=0.9).minimize(cost)

        # Option 2: tf.contrib.ctc.ctc_beam_search_decoder
        # (it's slower but you'll get better results)
        decoded, log_prob = tf.nn.ctc_greedy_decoder(logits, seq_len, merge_repeated=False)
        # decoded, log_prob = tf.nn.ctc_beam_search_decoder_v2(logits, seq_len, beam_width=5)

        # Inaccuracy: label error rate
        ler = tf.reduce_mean(tf.edit_distance(tf.cast(decoded[0], tf.int32),
                                              targets))

        stats = add_stats(cost, ler)

        saver = tf.train.Saver(max_to_keep=5, keep_checkpoint_every_n_hours=2)

    # Bookkeeping:
    time_window = ValueWindow(100)
    train_cost_window = ValueWindow(100)
    train_ler_window = ValueWindow(100)
    val_cost_window = ValueWindow(100)
    val_ler_window = ValueWindow(100)
    step_init = 0
    # Run!
    with tf.Session(graph=graph) as sess:
        summary_writer = tf.compat.v1.summary.FileWriter(log_dir, sess.graph)
        sess.run(tf.global_variables_initializer())

        if args.restore_step:
            # Restore from a checkpoint if the user requested it.
            restore_path = '%s-%d' % (checkpoint_path, args.restore_step)
            step_init = args.restore_step
            saver.restore(sess, restore_path)
            log('Resuming from checkpoint: %s' % (restore_path,))
        else:
            log('Starting new training run')

        # num_examples
        num_examples = 100
        
        num_batches_per_epoch = int(num_examples / batch_size)
        if which_data == 0:
            num_examples = dataset.n_training_set
            num_batches_per_epoch = int(num_examples / batch_size)
        elif which_data == 1:
            num_examples = dataset.__len__()
            num_batches_per_epoch = int(num_examples / batch_size)
            training_index = math.floor(num_examples * 0.8)
            testing_index = num_examples - training_index
            train_iter_time = 0
            test_iter_time = 0
        print("Number of validation example is {}".format(testing_index))
        for index_val in range(testing_index):
            if which_data == 0:
                val_inputs, val_targets, val_seq_len, indexR, val_original = next(dataset.next_testing_batch())
            elif which_data == 1:
                val_inputs, val_targets, val_seq_len, indexR, val_original = dataset.__getitem__(training_index + test_iter_time)
                if num_features == 1:
                    val_inputs = val_inputs[:, :, :1]
                test_iter_time += batch_size
                test_iter_time = test_iter_time % testing_index
            val_feed = {inputs: val_inputs,
                        targets: val_targets,
                        seq_len: val_seq_len}
            val_cost, val_ler = sess.run([cost, ler], feed_dict=val_feed)
            val_cost_window.append(val_cost)
            val_ler_window.append(val_ler)

        message = "avg_val_cost = {:.3f}, avg_val_ler = {:.3f}"
        log(message.format(val_cost_window.average, val_ler_window.average))
        # END OF TRAINING


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', default='./deepsniffer/training_randomgraphs/train.txt')
    parser.add_argument('--dataset', default='./obfuscator/dataset/train_data_dict.pickle')
    parser.add_argument('--model_path', default='"./obfuscator/predictor/logs_deepsniffer_LSTM_new_smart_normalize"')
    parser.add_argument('--restore_step', type=int, help='Global step to restore from checkpoint.')
    parser.add_argument('--normalize', type=str, default="sb", help='Pick normalization for the training data, need to match with the predictor', choices=("sb", "smart"))
    parser.add_argument('--train_type', type=str, default="reduced", help='Pick dataset you want to train on', choices=("reduced", "full", "time_only"))
    parser.add_argument('--summary_interval', type=int, default=1, help='Steps between running summary ops.')
    parser.add_argument('--checkpoint_interval', type=int, default=1, help='Steps between writing checkpoints.')
    parser.add_argument('--num_epochs', type=int, default=150, help='Number of Epochs for training.')
    parser.add_argument('--num_hidden', type=int, default=128, help='Number of hidden units for each LSTM layer.')
    parser.add_argument('--num_layers', type=int, default=1, help='Number of layer of LSTM.')
    parser.add_argument('--num_classes', type=int, default=10, help='Number of class for prediction')
    parser.add_argument('--batch_size', type=int, default=1, help='Batch size of sequence input')
    parser.add_argument('--tf_log_level', type=int, default=1, help='Tensorflow C++ log level.')
    args = parser.parse_args()

    os.environ['TF_CPP_MIN_LOG_LEVEL'] = str(args.tf_log_level)
    run_name = args.model_path
    log_dir = run_name
    os.makedirs(log_dir, exist_ok=True)
    infolog.init(os.path.join(log_dir, 'train.log'), run_name)
    if args.train_type == "reduced":
        num_features = 5
    elif args.train_type == "time_only":
        num_features = 1
    elif args.train_type == "full":
        num_features = 11
    run_ctc(num_features, log_dir, args)

if __name__ == '__main__':
    main()
