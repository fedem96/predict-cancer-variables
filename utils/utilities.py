from datetime import datetime
import random
import string

import numpy as np
import pandas as pd
import torch


def convert_date(date_str, format):
    try:
        date = datetime.strptime(date_str, format)
        return date
    except ValueError:
        return np.nan


def read_csv(dataset_name, encoding="utf-8", types={}, converters={}, dates_format={}, handle_nans={}, decimal='.'):
    cols = set().union(types, handle_nans, dates_format)
    converters.update({col: (lambda d, f=dates_format[col]: convert_date(d, f)) for col in dates_format})
    df = pd.read_csv(dataset_name, encoding=encoding, usecols=cols, converters=converters, dtype=types, decimal=decimal)
    replace_nulls(df, handle_nans)
    print("csv read")
    return df


def replace_nulls(df, replacements_dict):
    for col in replacements_dict:
        df.loc[df.index[df[col].isnull()], col] = replacements_dict[col]


def merge_and_extract(dataset, columns_names):
    merged_column = dataset[columns_names[0]]
    for column in columns_names[1:]:
        merged_column += " " + dataset[column]
    return merged_column.values
    # return dataset[columns_names].apply(lambda x: ' '.join(x), axis=1).values


def train_test_split(df, year_column, test_from_year):
    dfTest = df[df[year_column] >= test_from_year]
    dfTrain = df.drop(df.index[df[year_column] >= test_from_year])
    return dfTrain, dfTest


def bar(values, title=None, xlabel=None, ylabel=None, output_file=None):
    import matplotlib.pyplot as plt
    from collections import Counter

    fig, axes = plt.subplots(1, 1)

    counts = Counter(values)

    xs = counts.keys()
    heights = [counts[x] for x in xs]
    axes.bar(xs, heights)

    if title is not None: plt.title(title)
    if xlabel is not None: plt.xlabel(xlabel)
    if ylabel is not None: plt.ylabel(ylabel)

    if output_file is None:
        plt.show()
    else:
        plt.savefig(output_file)
        plt.clf()


def hist(values, title=None, xlabel=None, ylabel=None, cumulative=False, output_file=None):
    import matplotlib.pyplot as plt
    import math
    fig, axes = plt.subplots(1, 1)

    axes.hist(values, bins=math.ceil(max(values))-math.floor(min(values))+1, cumulative=cumulative)

    if title is not None: plt.title(title)
    if xlabel is not None: plt.xlabel(xlabel)
    if ylabel is not None: plt.ylabel(ylabel)

    if output_file is None:
        plt.show()
    else:
        plt.savefig(output_file)
        plt.clf()


def _annotate_heatmap(ax, annot_data):
    height, width = annot_data.shape
    max_val = annot_data.max()
    xpos, ypos = np.meshgrid(np.arange(width), np.arange(height))
    for x, y, val in zip(xpos.flat, ypos.flat, annot_data.flatten()):
        if val != 0:
            text_color = ".15" if val < max_val/2 else "w"
            annotation = ("{:.2g}").format(val)
            text_kwargs = dict(color=text_color, ha="center", va="center")
            ax.text(x, y, annotation, **text_kwargs)


def show_confusion_matrix(y_true, y_pred, output_file=None):  # TODO: add marginal accuracies
    import matplotlib.pyplot as plt
    from sklearn.metrics import confusion_matrix
    import seaborn as sns
    df = pd.DataFrame({"true": y_true, "pred": y_pred})
    df = df.dropna()
    m = confusion_matrix(y_true, y_pred)  # TODO: avoid nans
    ax = sns.jointplot(data=df, x="pred", y="true", kind="hist", cmap="Blues",
                  joint_kws={"discrete": True}, marginal_kws={"discrete": True})
    _annotate_heatmap(ax.ax_joint, m)

    if output_file is None:
        plt.show()
    else:
        plt.savefig(output_file)
        plt.clf()


def plot_bar(df, column, counts_threshold=0, sort_by_index=True, output_file=None):
    plot_bars(df, [column], counts_threshold, sort_by_index, output_file)


def plot_bars(df, columns, counts_threshold=0, sort_by_index=True, output_file=None):
    import matplotlib.pyplot as plt
    import math

    ncol = math.ceil(math.sqrt(len(columns)))
    nrow = math.ceil(len(columns) / ncol)

    fig, axes = plt.subplots(nrow, ncol)

    for col in range(len(columns)):
        r = col // ncol
        c = col % ncol
        counts = df[columns[col]].dropna().astype(str).value_counts()
        if sort_by_index:
            counts = counts.sort_index()

        ax = axes
        if len(columns) > 1 and nrow == 1:
            ax = axes[c]
        if nrow > 1:
            ax = axes[r, c]

        counts[counts > counts_threshold].dropna().plot(kind="bar", ax=ax)
        ax.set_title(columns[col])

    if output_file is None:
        plt.show()
    else:
        plt.savefig(output_file)
        plt.clf()


def plot_hist(df, column, output_file=None):
    plot_hists(df, [column], output_file)


def plot_hists(df, columns, output_file=None):
    import matplotlib.pyplot as plt
    import math

    ncol = math.ceil(math.sqrt(len(columns)))
    nrow = math.ceil(len(columns) / ncol)

    fig, axes = plt.subplots(nrow, ncol)

    for col in range(len(columns)):
        r = col // ncol
        c = col % ncol
        column = df[columns[col]].dropna()

        ax = axes
        if len(columns) > 1 and nrow == 1:
            ax = axes[c]
        if nrow > 1:
            ax = axes[r, c]

        if len(column) > 0:
            ax.hist(column, bins=range(math.floor(min(column)), math.ceil(max(column))))
            ax.set_title(columns[col])

    if output_file is None:
        plt.show()
    else:
        plt.savefig(output_file)
        plt.clf()


def random_name(model_name):
    return str(datetime.now()).split(".")[0].replace(":", ".").replace(" ", "_") + "_" + model_name + "_" + \
           ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(6))


def to_gpu_if_available(model):
    if torch.cuda.is_available():
        return model.to("cuda")
    return model