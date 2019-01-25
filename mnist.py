from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import numpy as np
import tensorflow as tf

from polyaxon_client.tracking import Experiment, get_data_paths, get_outputs_path

tf.logging.set_verbosity(tf.logging.INFO)

tf.app.flags.DEFINE_integer('steps', 100, 'The number of steps to train a model')
tf.app.flags.DEFINE_string('model_dir',
                           os.path.join(get_outputs_path(), "models", "ckpt"),
                           # "./models/ckpt/",
                           'Dir to save a model and checkpoints')
tf.app.flags.DEFINE_string('saved_dir',
                           os.path.join(get_outputs_path(), "models", "pb"),
                           # "./models/pb/",
                           'Dir to save a model for TF serving')
FLAGS = tf.app.flags.FLAGS

INPUT_FEATURE = 'image'
NUM_CLASSES = 10


def cnn_model_fn(features, labels, mode):
    """Model function for CNN."""
    # Input Layer
    input_layer = features[INPUT_FEATURE]

    # First convolutional Layer and pooling layer
    conv1 = tf.layers.conv2d(
        inputs=input_layer,
        filters=32,
        kernel_size=[5, 5],
        padding="same",
        activation=None)
    batch_norm1 = tf.layers.batch_normalization(conv1)
    relu1 = tf.nn.relu(batch_norm1)
    pool1 = tf.layers.max_pooling2d(inputs=relu1, pool_size=[2, 2], strides=2)

    # Second convolutional Layer and pooling layer
    conv2 = tf.layers.conv2d(
        inputs=pool1,
        filters=64,
        kernel_size=[5, 5],
        padding="same",
        activation=None)
    batch_norm2 = tf.layers.batch_normalization(conv2)
    relu2 = tf.nn.relu(batch_norm2)
    pool2 = tf.layers.max_pooling2d(inputs=relu2, pool_size=[2, 2], strides=2)

    # Flatten tensor into a batch of vectors
    pool2_flat = tf.layers.flatten(pool2)

    # Dense Layer
    dense = tf.layers.dense(inputs=pool2_flat, units=1024, activation=tf.nn.relu)

    # Add dropout operation
    dropout = tf.layers.dropout(
        inputs=dense, rate=0.4, training=(mode == tf.estimator.ModeKeys.TRAIN))

    # Logits layer
    logits = tf.layers.dense(inputs=dropout, units=NUM_CLASSES)

    predictions = {
        # Generate predictions (for PREDICT and EVAL mode)
        "classes": tf.argmax(input=logits, axis=1),
        # Add `softmax_tensor` to the graph. It is used for PREDICT and by the
        # `logging_hook`.
        "probabilities": tf.nn.softmax(logits, name="softmax_tensor")
    }

    # PREDICT mode
    if mode == tf.estimator.ModeKeys.PREDICT:
        return tf.estimator.EstimatorSpec(
            mode=mode,
            predictions=predictions,
            export_outputs={
                'predict': tf.estimator.export.PredictOutput(predictions)
            })

    # Calculate Loss (for both TRAIN and EVAL modes)
    loss = tf.losses.sparse_softmax_cross_entropy(labels=labels, logits=logits)

    # Configure the Training Op (for TRAIN mode)
    if mode == tf.estimator.ModeKeys.TRAIN:
        optimizer = tf.train.AdamOptimizer()
        train_op = optimizer.minimize(loss=loss, global_step=tf.train.get_global_step())
        return tf.estimator.EstimatorSpec(mode=mode, loss=loss, train_op=train_op)

    # Add evaluation metrics (for EVAL mode)
    eval_metric_ops = {
        "accuracy": tf.metrics.accuracy(labels=labels, predictions=predictions["classes"])
    }
    return tf.estimator.EstimatorSpec(mode=mode, loss=loss, eval_metric_ops=eval_metric_ops)


def serving_input_receiver_fn():
    """
    This is used to define inputs to serve the model.

    :return: ServingInputReciever
    """
    reciever_tensors = {
        # The size of input image is flexible.
        INPUT_FEATURE: tf.placeholder(tf.float32, [None, None, None, 1]),
    }

    # Convert give inputs to adjust to the model.
    features = {
        # Resize given images.
        INPUT_FEATURE: tf.image.resize_images(reciever_tensors[INPUT_FEATURE], [28, 28]),
    }
    return tf.estimator.export.ServingInputReceiver(receiver_tensors=reciever_tensors,
                                                    features=features)


def main(_):
    experiment = Experiment()
    tf_config = experiment.get_tf_config()
    print("=======================================")
    experiment.log_run_env()
    print(tf_config)

    # Load training and eval data
    mnist = tf.contrib.learn.datasets.load_dataset("mnist")
    train_data = mnist.train.images  # Returns np.array
    train_labels = np.asarray(mnist.train.labels, dtype=np.int32)
    eval_data = mnist.test.images  # Returns np.array
    eval_labels = np.asarray(mnist.test.labels, dtype=np.int32)

    # reshape images
    # To have input as an image, we reshape images beforehand.
    train_data = train_data.reshape(train_data.shape[0], 28, 28, 1)
    eval_data = eval_data.reshape(eval_data.shape[0], 28, 28, 1)

    # Create the Estimator
    # sess_config = tf.ConfigProto(
    #     device_count={'GPU': 1},
    #     allow_soft_placement=True,
    #     log_device_placement=True,
    #     gpu_options=tf.GPUOptions(force_gpu_compatible=True))
    training_config = tf.estimator.RunConfig(
        # session_config=sess_config,
        # model_dir=FLAGS.model_dir,
        save_summary_steps=20,
        save_checkpoints_steps=20)
    classifier = tf.estimator.Estimator(
        model_fn=cnn_model_fn,
        # model_dir=FLAGS.model_dir,
        config=training_config)

    # Set up logging for predictions
    # Log the values in the "Softmax" tensor with label "probabilities"
    tensors_to_log = {"probabilities": "softmax_tensor"}
    logging_hook = tf.train.LoggingTensorHook(tensors=tensors_to_log, every_n_iter=50)

    # Model exporter
    latest_exporter = tf.estimator.LatestExporter(
        name="models",
        serving_input_receiver_fn=serving_input_receiver_fn,
        exports_to_keep=5,
    )

    # Train the model
    train_input_fn = tf.estimator.inputs.numpy_input_fn(
        x={INPUT_FEATURE: train_data},
        y=train_labels,
        batch_size=FLAGS.steps,
        num_epochs=None,
        shuffle=True)
    train_spec = tf.estimator.TrainSpec(
        input_fn=train_input_fn,
        max_steps=1000000)

    # Evaluate the model and print results
    eval_input_fn = tf.estimator.inputs.numpy_input_fn(
        x={INPUT_FEATURE: eval_data},
        y=eval_labels,
        num_epochs=1,
        shuffle=False)
    eval_spec = tf.estimator.EvalSpec(
        input_fn=eval_input_fn,
        throttle_secs=180,
        steps=1000,
        exporters=latest_exporter,
    )

    # Train and eval
    tf.estimator.train_and_evaluate(classifier, train_spec=train_spec, eval_spec=eval_spec)


if __name__ == "__main__":
    tf.app.run()