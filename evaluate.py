import argparse
import json
from collections import defaultdict
from pprint import pprint

import numpy as np
import pandas as pd
import torch

from metrics.accuracy import Accuracy
from metrics.average import Average
from metrics.cks import CohenKappaScore
from metrics.dbcs import DumbBaselineComparisonScore
from metrics.mae import MeanAbsoluteError
from metrics.metrics import Metrics
from metrics.m_f1 import MacroF1Score
from metrics.nmae import NormalizedMeanAbsoluteError
from metrics.precision import Precision
from utils.chrono import Chronometer
from utils.constants import *
from utils.dataset import Dataset
from utils.serialization import load
from utils.utilities import merge_and_extract, to_gpu_if_available, show_confusion_matrix, show_regression_2Dkde

parser = argparse.ArgumentParser(description='Evaluate a model')
parser.add_argument("-d", "--dataset-dir", help="directory containing the dataset", default=os.path.join(DATASETS_DIR, NEW_DATASET), type=str)
parser.add_argument("-df", "--data-format", help="data format to use as input to the model", default="indices", type=str, choices=["indices", "tfidf"])
parser.add_argument("-ds", "--data-seed", help="seed for random data shuffling", default=None, type=int)
# parser.add_argument("-f", "--filter",
#                     help="report filtering strategy",
#                     default=None, type=str, choices=['same_year', 'classifier'], metavar='STRATEGY')
# parser.add_argument("-fa", "--filter-args",
#                     help="args for report filtering strategy",
#                     default=None, type=json.loads)
parser.add_argument("-gb", "--group-by",
                    help="list of (space-separated) grouping attributes to make multi-report predictions.",
                    default=None, nargs="+", type=str, metavar=('ATTR1', 'ATTR2'))
# parser.add_argument("-im", "--input-mappings", help="how to map the input", default={}, type=json.loads)
# parser.add_argument("-it", "--input-transformations", help="how to transform the input", default={}, type=json.loads)
parser.add_argument("-m", "--model", help="model to use", default=None, type=str, required=True)
# parser.add_argument("-ma", "--model-args", help="saved model to train", default=None, type=json.loads)
parser.add_argument("-ml", "--max-length", help="maximum sequence length (cut long sequences)", default=None, type=int)
# parser.add_argument("-rm", "--reduce-mode", help="how to reduce", default=None, type=str)
# parser.add_argument("-rt", "--reduce-type", help="what to reduce", default=None, type=str,
#                     choices=["data", "features", "logits", "eval"])
parser.add_argument("-s", "--set", help="set of the dataset", choices=["training", "validation", "test"], default="validation", type=str)
args = parser.parse_args()
print("args:", vars(args))
# if args.group_by is not None:
#     assert args.reduce_mode in {"data": {"most_recent"}, "features": {"max"}, "logits": {"mean"}, "eval": {"argmax"}}[args.reduce_type]  # TODO: multiple reduce modes

assert args.group_by is not None # TODO: without groupby not handled

input_cols = ["diagnosi", "macroscopia", "notizie"]
model = load(args.model)
model.eval()
model = to_gpu_if_available(model)
device = model.current_device()
print("model device:", device)
torch.set_grad_enabled(False)
classifications, regressions = model.get_validation_classifications(), model.get_validation_regressions()

dataset = Dataset(args.dataset_dir, args.set + "_set.csv", input_cols, max_report_length=args.max_length)
dataset.add_encoded_column(model.encode_report, dataset.encoded_data_column, dataset.max_report_length)
dataset.set_classifications(classifications)
dataset.set_regressions(regressions)
dataset.set_labels_codec(model.labels_codec)
dataset.encode_labels()

multi_layer = False
if args.group_by is not None:
    dataset.lazy_group_by(args.group_by)
    dataset.compute_lazy()
    multi_layer = True

if args.data_seed is not None:
    np.random.seed(args.data_seed)

data, labels = dataset.get_data(args.data_format), dataset.get_labels()

losses = {}
dumb_baseline_accuracy = {}
num_classes = {}
stds = {}
for var in classifications:
    classes_occurrences = labels[var].value_counts().sort_index().values
    num_classes[var] = len(classes_occurrences)
    classes_weights = 1 / classes_occurrences # TODO: cambiare pesi
    classes_weights = torch.from_numpy(classes_weights).float().to(model.current_device())
    losses[var] = torch.nn.CrossEntropyLoss(classes_weights) # TODO: spesso non la calcola
    dumb_baseline_accuracy[var] = max(classes_occurrences) / sum(classes_occurrences)
for var in regressions:
    losses[var] = torch.nn.MSELoss()
    stds[var] = labels[var].dropna().values.std()

def create_losses(classifications, regressions):
    return {
        "Loss": {var: Average(min) for var in list(classifications) + list(regressions)}
    }

def create_classification_metrics(classifications):
    return {
        "Accuracy": {var: Accuracy() for var in classifications},
        "M-F1": {var: MacroF1Score() for var in classifications},
        "CKS": {var: CohenKappaScore() for var in classifications},
        "DBCS": {var: DumbBaselineComparisonScore(dumb_baseline_accuracy[var]) for var in classifications},
        "M-Precision": {var + "_" + str(cls): Precision(cls) for var in classifications for cls in range(dataset.nunique(var))}
    }


def create_regression_metrics(regressions):
    return {
        "MAE": {var: MeanAbsoluteError() for var in regressions},
        "NMAE": {var: NormalizedMeanAbsoluteError(stds[var]) for var in regressions}
    }


classification_metrics = create_classification_metrics(classifications)
regression_metrics = create_regression_metrics(regressions)

metrics = Metrics({**create_losses(classifications, regressions), **classification_metrics,
                   **regression_metrics})

data = torch.tensor(data.astype(np.int16), device=device)
batch_size = 64
num_batches = len(data) // batch_size
metrics.reset()
y_preds = defaultdict(lambda: [])
with Chronometer("calculating metrics"):
    for b in range(num_batches):
        batch = data[b * batch_size: (b + 1) * batch_size]
        batch_labels = labels.iloc[b * batch_size: (b + 1) * batch_size].reset_index()
        forwarded = model(batch)
        for var in classifications:
            mask = ~batch_labels[var].isnull()
            if mask.sum() == 0:
                continue
            preds = forwarded[var][mask].squeeze(2).squeeze(1)
            grth = torch.tensor(batch_labels[var][mask].to_list(), dtype=torch.long, device=device, requires_grad=False).cpu().numpy()
            loss = metrics.metrics["Loss"][var](preds, grth) / len(grth)
            preds_classes = torch.argmax(preds.detach(), dim=1).cpu().numpy()
            y_preds[var].append(preds_classes)

            metrics.metrics["Loss"][var].update(loss, num_batches)
            for metric_name in classification_metrics:
                if var in metrics.metrics[metric_name]:
                    metrics.metrics[metric_name][var].update(preds_classes, grth)
                else:
                    for cls in range(dataset.nunique(var)):
                        metrics.metrics[metric_name][var + "_" + str(cls)].update(preds_classes, grth)

        for var in regressions:
            mask = ~batch_labels[var].isnull()
            if mask.sum() == 0:
                continue
            preds = forwarded[var][mask].squeeze(3).squeeze(2).squeeze(1).cpu()
            grth = torch.tensor(batch_labels[var][mask].to_list(), requires_grad=False)
            loss = losses[var](preds, grth) / len(grth)
            y_preds[var].append(preds)
            metrics.metrics["Loss"][var].update(loss.item(), num_batches)
            for metric_name in regression_metrics:
                metrics.metrics[metric_name][var].update(preds, grth)

pprint(metrics)

evaluate_dir = os.path.join(args.model + "_evaluate", args.set)
if not os.path.exists(evaluate_dir):
    os.makedirs(evaluate_dir)

with open(os.path.join(evaluate_dir, "metrics.json"), "wt") as file:
    json.dump(json.loads(str(metrics).replace("'", '"')), file)

for var in classifications:
    directory = os.path.join(evaluate_dir, var)
    if not os.path.exists(directory):
        os.makedirs(directory)
    y_pred = np.concatenate(y_preds[var])
    y_true = labels[var].dropna().to_numpy().astype(int)[:len(y_pred)]
    with open(os.path.join(directory, "errors.json"), 'w') as errors_file:
        errors = [dataset.dataframe[i]['id_paz'].unique().item() for i in np.where(y_pred != y_true)[0].tolist()]
        json.dump(errors, errors_file)
    show_confusion_matrix(y_true, y_pred, var +"\n", os.path.join(directory, "confusion_matrix.png"))

for var in regressions:
    directory = os.path.join(evaluate_dir, var)
    if not os.path.exists(directory):
        os.makedirs(directory)
    y_pred = np.concatenate(y_preds[var])
    y_true = labels[var].dropna().to_numpy().astype(float)[:len(y_pred)]
    show_regression_2Dkde(y_true, y_pred, var +"\n", os.path.join(directory, "pred_grth_density.png"))
