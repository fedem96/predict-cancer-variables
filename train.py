import argparse
import json
from importlib import import_module

from callbacks.early_stopping import EarlyStoppingSW
from callbacks.logger import MetricsLogger
from callbacks.model_checkpoint import ModelCheckpoint
from utils.chrono import Chronostep
from utils.constants import *
from utils.dataset import Dataset
from utils.utilities import *

parser = argparse.ArgumentParser(description='Train a model')
parser.add_argument("-ap", "--activation-penalty", help="weight for activation (abs) regularization", default=0, type=float)
parser.add_argument("-b", "--batch-size", help="batch size to use for training", default=128, type=int)
parser.add_argument("-cp", "--copy", help="copy columns", default=[], nargs="+", type=str)
parser.add_argument("-cd", "--classifiers-dropout", help="dropout before each classifier", default=0, type=float)
parser.add_argument("-cl2p", "--classifiers-l2-penalty", help="l2 penalty for each classifier", default=0, type=float)
parser.add_argument("-cr", "--concatenate-reports", help="whether to concatenate reports of the same record before training", default=False, action='store_true')
parser.add_argument("-d", "--dataset-dir", help="directory containing the dataset", default=os.path.join(DATASETS_DIR, NEW_DATASET), type=str)
parser.add_argument("-df", "--data-format", help="data format to use as input to the model", default="indices", type=str, choices=["indices", "tfidf"])
parser.add_argument("-ds", "--data-seed", help="seed for random data shuffling", default=None, type=int)
parser.add_argument("-e", "--epochs", help="number of maximum training epochs", default=50, type=int)
parser.add_argument("-f", "--filter", help="report filtering strategy",
                    default=None, type=str, choices=['same_year', 'classifier'], metavar='STRATEGY')
parser.add_argument("-fa", "--filter-args", help="args for report filtering strategy", default=None, type=json.loads)
parser.add_argument("-gb", "--group-by", help="list of (space-separated) grouping attributes to make multi-report predictions.",
                    default=None, nargs="+", type=str, metavar=('ATTR1', 'ATTR2'))
parser.add_argument("-grl", "--gradient-reversal-lambda", help="value of lambda for each Gradient Reversal Layer", default=0.05, type=float)
parser.add_argument("-ic", "--input-cols", help="list of input columns names", default=["diagnosi", "macroscopia", "notizie"], nargs="+", type=str)
parser.add_argument("-lr", "--learning-rate", help="learning rate for Adam optimizer", default=0.00001, type=float)
parser.add_argument("-lrs", "--learning-rate-scheduler", help="type of learning rate scheduler", default=None, type=str, choices=["StepLR"]) # TODO: complete
parser.add_argument("-lrsa", "--learning-rate-scheduler-args", help="args for learning rate scheduler", default={}, type=json.loads) # TODO: complete
parser.add_argument("-lp", "--labels-preprocessing", help="how to preprocess the labels", default={}, type=json.loads)
parser.add_argument("-m", "--model", help="model to train", default=None, type=str, required=True)
parser.add_argument("-ma", "--model-args", help="model to train", default=None, type=json.loads)
parser.add_argument("-ng", "--n-grams", help="n of the n-grams", default=1, type=int, choices=range(1,6))
parser.add_argument("-ns", "--net-seed", help="seed for model random weights generation", default=None, type=int)
parser.add_argument("-ml", "--max-length", help="maximum sequence length (cut long sequences)", default=None, type=int)
parser.add_argument("-mtl", "--max-total-length", help="maximum sequence length after concatenation (cut long sequences)", default=None, type=int)
parser.add_argument("-ms", "--max-size", help="maximum size of the records (i.e. maximum reports per record)", default=None, type=int)
parser.add_argument("-n", "--name", help="name to use when saving the model", default=None, type=str)
parser.add_argument("-o", "--out", help="file where to save best values of the metrics", default=None, type=str) # TODO: add print best
parser.add_argument("-opt", "--optimizer", help="name of the optimizer", default="Adam", type=str, choices=["Adam", "AdamW", "SGD"]) # TODO: finish to implement
parser.add_argument("-opta", "--optimizer-args", help="args for the optimizer", default={}, type=json.loads)
parser.add_argument("-pr", "--pool-reports", help="whether (and how) to pool reports (i.e. aggregate features of reports in the same record)", default=None, type=str, choices=["max"])
parser.add_argument("-pt", "--pool-tokens", help="how to pool tokens (i.e. aggregate features of tokens in the same report)", default=None, choices=["max"])
parser.add_argument("-rd", "--regressors-dropout", help="dropout before each regressor", default=0, type=float)
parser.add_argument("-rl2p", "--regressors-l2-penalty", help="l2 penalty for each regressor", default=0, type=float)
parser.add_argument("-rt", "--reports-transformation", help="how to transform reports (i.e. how to obtain a deep representation of the reports)", default="identity", choices=["identity", "transformer"])
parser.add_argument("-rta", "--reports-transformation-args", help="args for report transformation", default={}, type=json.loads)
parser.add_argument("-tac", "--train-anti-classifications", help="list of classifications preceded by a Gradient Reversal Layer", default=[], nargs="+", type=str)
parser.add_argument("-tar", "--train-anti-regressions", help="list of regressions preceded by a Gradient Reversal Layer", default=[], nargs="+", type=str)
parser.add_argument("-tc", "--train-classifications", help="list of classifications", default=[], nargs="+", type=str)
parser.add_argument("-to", "--training-set-only", help="list of variables to predict only on the training set", default=[], nargs="+", type=str)
parser.add_argument("-tr", "--train-regressions", help="list of regressions", default=[], nargs="+", type=str)
args = parser.parse_args()
print("args:", vars(args))

with Chronostep("reading input"):
    classifications, regressions, anti_classifications, anti_regressions = args.train_classifications, args.train_regressions, args.train_anti_classifications, args.train_anti_regressions
    transformations = args.labels_preprocessing


def full_pipe(report):
    return t.tokenize(p.preprocess(report), encode=True)

with Chronostep("encoding reports"):
    tokenizer_file_name = f"tokenizer-{args.n_grams}gram.json"
    sets = {
        "train": Dataset(args.dataset_dir, TRAINING_SET, args.input_cols, tokenizer_file_name, max_report_length=args.max_length, max_record_size=args.max_size),
        "val": Dataset(args.dataset_dir, VALIDATION_SET, args.input_cols, tokenizer_file_name, max_report_length=args.max_length, max_record_size=args.max_size)
    }
    training, validation = sets["train"], sets["val"]

    for set_name in ["train", "val"]:
        dataset = sets[set_name]

        t = dataset.tokenizer
        p = dataset.preprocessor
        dataset.add_encoded_column(full_pipe, dataset.encoded_data_column, dataset.max_report_length)
        for c in range(0, len(args.copy), 2):
            dataset.copy_column(args.copy[c], args.copy[c+1])
        if set_name == "train":
            dataset.set_classifications(classifications + anti_classifications)
            dataset.set_regressions(regressions + anti_regressions)
            labels_codec = dataset.create_labels_codec(transformations)
        else:
            dataset.set_classifications([var for var in classifications + anti_classifications if var not in args.training_set_only])
            dataset.set_regressions([var for var in regressions + anti_regressions if var not in args.training_set_only])
            dataset.set_labels_codec(labels_codec)

        dataset.encode_labels()

        dataset.remove_examples_without_labels()

        if args.group_by is not None:

            dataset.lazy_group_by(args.group_by)

            if args.filter is not None:
                dataset.lazy_filter(args.filter, args.filter_args)

            if args.concatenate_reports:
                dataset.lazy_concatenate_reports(args.max_total_length)

            dataset.compute_lazy()

    if args.group_by is not None:
        training.assert_disjuncted(validation)

    # training.limit(16840)
    # validation.limit(2048)
print(f"number of tokens: {training.tokenizer.num_tokens()}")
with Chronostep("getting labels"):
    training_labels = training.get_labels()
    validation_labels = validation.get_labels()
with Chronostep("calculating labels distributions"):
    for column in training.classifications:
        print("training")
        print(training_labels[column].value_counts()) # TODO: show also percentages
        if column in validation_labels:
            print("validation")
            print(validation_labels[column].value_counts())
            print()
    for column in training.regressions:
        print(column)
        print("training")
        print("min:", training_labels[column].min())
        print("max:", training_labels[column].max())
        print("mean:", training_labels[column].mean())
        print("std:", training_labels[column].std())
        if column in validation_labels:
            print("validation")
            print("min:", validation_labels[column].min())
            print("max:", validation_labels[column].max())
            print("mean:", validation_labels[column].mean())
            print("std:", validation_labels[column].std())
            print()

with Chronostep("creating model"):
    model_name = args.name or random_name(str(args.model).split(".")[-1])
    model_dir = os.path.join(TRAINED_MODELS_DIR, model_name)
    os.makedirs(model_dir)

    with open(os.path.join(model_dir, "args.json"), "wt") as file:
        json.dump(vars(args), file)

    module, class_name = args.model.rsplit(".", 1)
    Model = getattr(import_module(module), class_name)
    model_args = {
        "vocab_size": training.tokenizer.num_tokens()+1, "preprocessor": training.preprocessor, "tokenizer": training.tokenizer,
        "labels_codec": training.get_labels_codec(), "directory": model_dir
    }
    if args.model_args is not None:
        model_args.update(args.model_args)
    if args.net_seed is not None:
        model_args.update({"net_seed": args.net_seed})
    model = Model(**model_args)
    model = to_gpu_if_available(model)
    model.set_tokens_pooling_method(args.pool_tokens)
    model.set_reports_transformation_method(args.reports_transformation, **args.reports_transformation_args)
    model.set_reports_pooling_method(args.pool_reports)

    for cls_var in classifications:
        model.add_classification(cls_var, training.nunique(cls_var), args.classifiers_dropout, args.classifiers_l2_penalty, cls_var in args.training_set_only)

    for reg_var in regressions:
        model.add_regression(reg_var, args.regressors_dropout, args.classifiers_l2_penalty, reg_var in args.training_set_only)

    for cls_var in anti_classifications:
        model.add_anti_classification(cls_var, training.nunique(cls_var), args.classifiers_dropout, args.classifiers_l2_penalty, args.gradient_reversal_lambda, cls_var in args.training_set_only)

    for reg_var in anti_regressions:
        model.add_anti_regression(reg_var, args.regressors_dropout, args.regressors_l2_penalty, args.gradient_reversal_lambda, reg_var in args.training_set_only)

print("created model: " + model_name)
print("model device:", model.current_device())

print("parameters:", sum([p.numel() for p in model.parameters()]))
# for parameter_name, parameter in model.named_parameters():
#     if "classifier" in parameter_name:
#         parameter.requires_grad = False
for parameter_name, parameter in model.named_parameters():
    print("\t{}: {}, trainable: {}".format(parameter_name, parameter.numel(), parameter.requires_grad))

hyperparameters = {
    "batch_size": args.batch_size, "learning_rate": args.learning_rate, "max_epochs": args.epochs,
    "activation_penalty": args.activation_penalty, "classifiers_l2_penalty": args.classifiers_l2_penalty, "regressors_l2_penalty": args.regressors_l2_penalty
}
with open(os.path.join(model_dir, "hyperparameters.json"), "wt") as file:
    json.dump(hyperparameters, file)

info = {**{k: v for k, v in vars(args).items() if k in {"data_seed", "net_seed", "filter", "filter_args", "concatenate_reports"
                                                        "model_args", "pool_reports", "pool_tokens"}},
        "name": model_name, "dataset": args.dataset_dir.split(".")[-1]}

if args.data_seed is not None:
    np.random.seed(args.data_seed)

# from utils.utilities import hist
# hist([len(ex) for ex in training.get_data(DATA_COL)])

tb_dir = os.path.join(model_dir, "logs")
callbacks = [MetricsLogger(terminal='table', tensorboard_dir=tb_dir, aim_name=model.__name__, history_size=10),
             ModelCheckpoint(model_dir, 'Loss', verbose=True, save_best=True),
             #EarlyStoppingSW('Loss', min_delta=1e-5, patience=10, verbose=True, from_epoch=10)
            ]

with Chronostep("training model '{}'".format(model_name)):
    model.fit(training.get_data(args.data_format), training_labels, validation.get_data(args.data_format), validation_labels, info, callbacks, **hyperparameters)

# TODO: clean code and apis
# TODO: speedup script
# TODO: experiment
