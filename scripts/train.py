#!/usr/bin/env python

import os
import time
import logging
import argparse
import tensorflow as tf

import utils
import pooling_layers
from model import SocialModel
from coordinates_helpers import train_helper
from losses import social_loss_function
from position_estimates import social_train_position_estimate


def logger(hparams, args):
    log_file = hparams.name + "-train.log"
    log_folder = None
    level = "INFO"
    formatter = logging.Formatter(
        "[%(asctime)s %(filename)s] %(levelname)s: %(message)s"
    )

    # Check if you have to add a FileHandler
    if args.logFolder is not None:
        log_folder = args.logFolder
    elif hparams.logFolder is not None:
        log_folder = hparams.logFolder

    if log_folder is not None:
        log_file = os.path.join(log_folder, log_file)
        if not os.path.exists(log_folder):
            os.makedirs(log_folder)

    # Set the level
    if args.logLevel is not None:
        level = args.logLevel.upper()
    elif hparams.logLeve is not None:
        level = hparams.logLevel.upper()

    # Get the logger
    logger = logging.getLogger()
    # Remove handlers added previously
    for handler in logger.handlers[:]:
        if isinstance(handler, logging.FileHandler):
            handler.close()
        logger.removeHandler(handler)
    if log_folder is not None:
        # Add a FileHandler
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    # Add a StreamHandler that display on sys.stderr
    stderr_handler = logging.StreamHandler()
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)

    # Set the level
    logger.setLevel(level)


def main():
    # Parse the arguments received from command line
    parser = argparse.ArgumentParser(description="Train a social LSTM")
    parser.add_argument(
        "modelParams",
        type=str,
        help="Path to the file or folder with the parameters of the experiments",
    )
    parser.add_argument(
        "-l",
        "--logLevel",
        help="logging level of the logger. Default is INFO",
        metavar="level",
        type=str,
    )
    parser.add_argument(
        "-f",
        "--logFolder",
        help="path to the folder where to save the logs. If None, logs are only printed in stderr",
        type=str,
        metavar="path",
    )
    args = parser.parse_args()

    if os.path.isdir(args.modelParams):
        names_experiments = os.listdir(args.modelParams)
        experiments = [
            os.path.join(args.modelParams, experiment)
            for experiment in names_experiments
        ]
    else:
        experiments = [args.modelParams]

    for experiment in experiments:
        # Load the parameters
        hparams = utils.YParams(experiment)
        # Define the logger
        logger(hparams, args)

        remainSpaces = 29 - len(hparams.name)
        logging.info(
            "\n"
            + "--------------------------------------------------------------------------------\n"
            + "|                            Training experiment: "
            + hparams.name
            + " " * remainSpaces
            + "|\n"
            + "--------------------------------------------------------------------------------\n"
        )

        trajectory_size = hparams.obsLen + hparams.predLen

        logging.info("Loading the training datasets...")
        train_loader = utils.DataLoader(
            hparams.dataPath,
            hparams.trainDatasets,
            delimiter=hparams.delimiter,
            skip=hparams.skip,
            max_num_ped=hparams.maxNumPed,
            trajectory_size=trajectory_size,
        )
        logging.info("Loading the validation datasets...")
        val_loader = utils.DataLoader(
            hparams.dataPath,
            hparams.validationDatasets,
            delimiter=hparams.delimiter,
            skip=hparams.skip,
            max_num_ped=hparams.maxNumPed,
            trajectory_size=trajectory_size,
        )

        logging.info("Creating the training and validation dataset pipeline...")
        dataset = utils.TrajectoriesDataset(
            train_loader,
            val_loader=val_loader,
            batch=False,
            prefetch_size=hparams.prefetchSize,
        )

        logging.info("Creating the helper for the coordinates")
        helper = train_helper

        pooling_module = None
        if isinstance(hparams.poolingModule, list):
            logging.info(
                "Creating the combined pooling: {}".format(hparams.poolingModule)
            )
            pooling_class = pooling_layers.CombinedPooling(hparams)
            pooling_module = pooling_class.pooling

        elif hparams.poolingModule == "social":
            logging.info("Creating the {} pooling".format(hparams.poolingModule))
            pooling_class = pooling_layers.SocialPooling(hparams)
            pooling_module = pooling_class.pooling

        elif hparams.poolingModule == "occupancy":
            logging.info("Creating the {} pooling".format(hparams.poolingModule))
            pooling_class = pooling_layers.OccupancyPooling(hparams)
            pooling_module = pooling_class.pooling

        hparams.add_hparam("learningRateSteps", train_loader.num_sequences)

        logging.info("Creating the model...")
        start = time.time()
        model = SocialModel(
            dataset,
            helper,
            social_train_position_estimate,
            social_loss_function,
            pooling_module,
            hparams,
        )
        end = time.time() - start
        logging.debug("Model created in {:.2f}s".format(end))

        # Define the path to where save the model and the checkpoints
        if hparams.modelFolder:
            save_model = True
            model_folder = os.path.join(hparams.modelFolder, hparams.name)
            if not os.path.exists(model_folder):
                os.makedirs(model_folder)
                os.makedirs(os.path.join(model_folder, "checkpoints"))
            model_path = os.path.join(model_folder, hparams.name)
            checkpoints_path = os.path.join(model_folder, "checkpoints", hparams.name)
            # Create the saver
            saver = tf.train.Saver()

        # ============================ START TRAINING ============================

        with tf.Session() as sess:
            logging.info(
                "\n"
                + "--------------------------------------------------------------------------------\n"
                + "|                                Start training                                |\n"
                + "--------------------------------------------------------------------------------\n"
            )
            # Initialize all the variables in the graph
            sess.run(tf.global_variables_initializer())

            for epoch in range(hparams.epochs):
                logging.info("Starting epoch {}".format(epoch + 1))

                # ==================== TRAINING PHASE ====================

                # Initialize the iterator of the training dataset
                sess.run(dataset.init_train)

                for sequence in range(train_loader.num_sequences):
                    start = time.time()
                    loss, _ = sess.run([model.loss, model.trainOp])
                    end = time.time() - start

                    logging.info(
                        "{}/{} epoch: {} time/Batch = {:.2f}s. Loss = {:.4f}".format(
                            sequence + 1,
                            train_loader.num_sequences,
                            epoch + 1,
                            end,
                            loss,
                        )
                    )

                # ==================== VALIDATION PHASE ====================

                logging.info(" ========== Validation ==========")
                # Initialize the iterator of the validation dataset
                sess.run(dataset.init_val)
                loss_val = 0

                for _ in range(val_loader.num_sequences):
                    loss = sess.run(model.loss)
                    loss_val += loss

                mean_val = loss_val / val_loader.num_sequences

                logging.info(
                    "Epoch: {}. Validation loss = {:.4f}".format(epoch + 1, mean_val)
                )

                # Save the model
                if save_model:
                    logging.info("Saving model...")
                    saver.save(
                        sess,
                        checkpoints_path,
                        global_step=epoch + 1,
                        write_meta_graph=False,
                    )
                    logging.info("Model saved...")
            # Save the final model
            if save_model:
                saver.save(sess, model_path)
        tf.reset_default_graph()


if __name__ == "__main__":
    main()
