import copy
import json
import matplotlib.pyplot as plt
import mpld3  # needs this fix:

# https://stackoverflow.com/questions/47380865/json-serialization-error-using-matplotlib-mpld3-with-linkedbrush
import numpy as np
import os
import pandas as pd
import re
import spacy
import sys
import tempfile

from seaborn import heatmap
from collections import Counter
from itertools import chain, permutations
from string import ascii_lowercase
from scipy.stats import mode
from sklearn.decomposition import PCA
from sklearn.cluster import KMeans
from sklearn.manifold import TSNE
from sklearn.cluster import SpectralClustering
from sklearn.neighbors.nearest_centroid import NearestCentroid
from sklearn.neighbors import NearestNeighbors
from sklearn.mixture import GaussianMixture, BayesianGaussianMixture
from sklearn.metrics import (
    silhouette_score,
    silhouette_samples,
    homogeneity_score,
    confusion_matrix,
)

from contexts import Contexts, LabeledContexts
from transformembeddings import ContextEmbeddings

############################################################
# Plot: PCA
############################################################


def plot_decomposed_embeddings(ce_object):
    if ce_object.decomp_dims == 2:
        fig, ax = plt.subplots(figsize=(10, 8))

        points = ax.plot(
            ce_object.decomposed_embedding_representation[:, 0],
            ce_object.decomposed_embedding_representation[:, 1],
            "o",
            color="b",
            mec="k",
            ms=5,
            mew=1,
            alpha=0.6,
        )

        ax.set_title(
            f"{ce_object.decomp_dims}D {ce_object.decomp_method} for contexts of '{ce_object.word}'",
            size=16,
        )

        labels = [ce_object.contexts[i] for i in range(len(ce_object.contexts))]
        tooltip = mpld3.plugins.PointHTMLTooltip(
            points[0], labels, voffset=10, hoffset=10
        )
        mpld3.plugins.connect(fig, tooltip)

        mpld3.enable_notebook()

    else:
        print("Can't plot with dimension > 2.")


def plot_scree_PCA(ce_object):
    if hasattr(ContextEmbeddings, "explained_variance_ratio_"):
        plt.hlines(y=0.99, xmin=0, xmax=ce_object.decomp_dims)
        plt.plot(np.cumsum(ce_object.explained_variance_ratio_))
        plt.xlabel("Number of principal components")
        plt.ylabel("Cumulative explained variance")
        plt.title("Variance explained by principal components")
        plt.show()

    else:
        print("Scree plot only available for PCA.")


##############################
# Visualizing and inspecting cluster quality
##############################


def compute_silhouette(ce_object, use_decomposed):
    """
    Computes mean silhouette score and silhouette score by cluster
    Helper function for cluster_and_silhouette()

    """
    if use_decomposed:
        vecs_df = ce_object.decomposed_embedding_representation
    else:
        vecs_df = ce_object.embedding_representation

    mean_silhouette_score = silhouette_score(vecs_df, ce_object.predicted_labels)
    silhouette_sample_scores = silhouette_samples(vecs_df, ce_object.predicted_labels)

    cluster_fit = pd.DataFrame(
        {
            "predicted_cluster": ce_object.predicted_labels,
            "silhouette_score": ce_object.silhouette_sample_scores,
        }
    )

    print(cluster_fit.groupby("predicted_cluster").mean())


def print_cluster_examples(ce_object, num_print=10):
    """
    num_print : (max) number of contexts to print per cluster

    Returns:
        prints example word contexts per cluster/dimension
    """
    if ce_object.decomp_method:
        decomposed = ce_object.decomposed_embedding_representation
    else:
        decomposed = ce_object.embedding_representation

    for k in range(ce_object.num_clusters):
        print(f"\n==== {k} ==== (max {num_print} examples per cluster)")
        for counter, ii in enumerate(np.where(ce_object.predicted_labels == k)[0]):
            if counter < num_print:
                print(ce_object.contexts[ii])


############################################################
# Methods for ce_object with labels
############################################################


def plot_labeled_data(ce_object, mpl=True):
    """
    Plots decomposed data in 2D with colors representing hand labeled clusters

    Returns:
        Plots interactive figure with points colored by hand-coded label
    """
    if not ce_object.true_labels:
        print("Can't plot without true data labels!")

    if ce_object.decomp_dims < 2:
        print("Can't plot in higher than 2D!")

    color_labels = [
        "red" if i == 1 else "green" if i == 0 else "blue"
        for i in ce_object.true_labels
    ]

    decomp_df = pd.DataFrame(ce_object.decomposed_embedding_representation)
    decomp_df.columns = ["dim1", "dim2"]
    decomp_df["color"] = color_labels

    fig, ax = plt.subplots(figsize=(10, 8))

    scatter = ax.scatter(
        decomp_df["dim1"],
        decomp_df["dim2"],
        c=decomp_df["color"],
        alpha=0.5,
        cmap=plt.cm.jet,
    )

    ax.set_title(
        f"2D {ce_object.decomp_method} for contexts of '{ce_object.word}'", size=20
    )

    labels = [ce_object.contexts[i] for i in range(decomp_df.shape[0])]
    tooltip = mpld3.plugins.PointLabelTooltip(scatter, labels=labels)
    mpld3.plugins.connect(fig, tooltip)

    mpld3.save_html(fig, "plot.html")
    mpld3.enable_notebook()


def evaluate_cluster_quality(ce_object):
    """
    Given ground truth labels, performs clustering of vector representations of a word
    (after optionally decomposing vector representations) and compares predicted and true labels.

    """
    print(
        f"Using {ce_object.cluster_method} with k={ce_object.num_clusters},\n \
        and {ce_object.decomp_method} prior to clustering:"
    )

    print("Homogeneity score: \n")
    print(homogeneity_score(ce_object.true_labels, ce_object.predicted_labels))

    if len(set(ce_object.true_labels)) != len(set(ce_object.predicted_labels)):
        print(
            "Cannot compute confusion matrix: \n \
            The number of true classes is not equal to the number of predicted classes."
        )
        return

    print("\n\nNormalized confusion matrix:")

    cm = ce_object.optimize_mapping_cm(
        ce_object.true_labels, ce_object.predicted_labels
    )

    print(cm)
    plt.figure(figsize=(8, 6))
    heatmap(cm, annot=True, cmap="YlGnBu", square=True)


def optimize_mapping_cm(true_labels, predicted_labels):
    """
    Helper function that finds the best mapping of predicted_labels (which have no meaning)
    to true_labels (which were hand coded)
    """
    best_mat = None
    best_diag = 0

    # placeholders so that value swaps can occur later
    num_unique_labels = len(set(predicted_labels))
    alpha_to_label_mapping = dict(
        list(zip(range(num_unique_labels), ascii_lowercase[:num_unique_labels]))
    )
    predicted_labels_placeholder = [alpha_to_label_mapping[i] for i in predicted_labels]

    possible_label_orders = list(permutations(range(num_unique_labels)))

    for label_order in possible_label_orders:
        label_mapping = dict(
            list(zip(ascii_lowercase[:num_unique_labels], label_order))
        )

        this_label_mapping = [label_mapping[i] for i in predicted_labels_placeholder]
        this_cm = confusion_matrix(true_labels, this_label_mapping)
        this_cm = this_cm.astype("float") / this_cm.sum(axis=1)[:, np.newaxis]
        this_mat_diag = np.sum(np.diagonal(this_cm))

        if this_mat_diag > best_diag:
            best_diag = this_mat_diag
            best_mat = this_cm

    return best_mat


def get_centroids(ce_object):
    """
    Given hand labeled cluster assignments (optionally decomposed), return cluster centroid.
    Helper function to be called in find_contexts_neighboring_centroid()

    Returns:
        numpy array of representations of centroids
    """

    if ce_object.decomp_method:
        X = ce_object.decomposed_embedding_representation
    else:
        X = ce_object.embedding_representation.values

    y = np.array(ce_object.predicted_labels)

    clf = NearestCentroid()
    clf.fit(X, y)

    return clf.centroids_


def find_contexts_neighboring_centroid(ce_object, num_neighbors=20):
    """
    Given a set of word contexts with cluster labels,
    stores the n closest contexts to each cluster centroid.

        num_neighbors : number of neighbors to return, default = 20
    """
    cluster_centroids = get_centroids(ce_object)

    if ce_object.decomp_method:
        vecs_df = ce_object.decomposed_embedding_representation
    else:
        vecs_df = ce_object.embedding_representation

    contexts_near_centroids = dict()
    context_near_centroids_tf_idf_dicts = dict()

    for i in range(ce_object.num_clusters):
        centroid = cluster_centroids[i,]

        nn = NearestNeighbors()
        nn.fit(vecs_df)
        neighbor_indices = nn.kneighbors(
            [centroid], n_neighbors=num_neighbors, return_distance=False
        ).flatten()

        centroid_contexts = [ce_object.spacy_contexts[i] for i in neighbor_indices]
        contexts_near_centroids[i] = centroid_contexts
        # each centroid gets a list of spacy_contexts near it

        # each centroid needs tf_idf_dicts for its contexts
        tf_idf_dicts_for_centroid_i = [
            ce_object.tf_idf_dicts[j] for j in neighbor_indices
        ]  # [Dict]
        context_near_centroids_tf_idf_dicts[i] = tf_idf_dicts_for_centroid_i

    return contexts_near_centroids, context_near_centroids_tf_idf_dicts


def rank_words_near_centroid(ce_object, num_words, tf_idf_weight=False):
    """
        centroids_top_words is a Dict{keys = centroid numbers,
        values = dataframe of words near that centroid in descending order}

        Excludes punctuation and stopwords
    """

    contexts_near_centroids, context_near_centroids_tf_idf_dicts = find_contexts_neighboring_centroid(
        ce_object
    )

    nlp = spacy.load("en_core_web_sm")

    centroids_top_words = dict()

    tokens_overall_weighted = Counter({})

    with nlp.disable_pipes("tagger", "ner", "parser"):
        for i in range(ce_object.num_clusters):
            cluster_centroid_neighbors = contexts_near_centroids[i]
            cluster_centroid_dicts = context_near_centroids_tf_idf_dicts[i]

            for index, tokenized_context in enumerate(cluster_centroid_neighbors):
                token_weighted_frequencies = (
                    {}
                )  # dict with only one element (loop below is one token)

                for token in tokenized_context:
                    token_weight = (
                        cluster_centroid_dicts[index].get(token, 1)
                        if tf_idf_weight
                        else 1
                    )
                    token_weighted_frequencies[token] = token_weight
                    tokens_overall_weighted += Counter(token_weighted_frequencies)

            tokens_df = pd.DataFrame.from_dict(
                tokens_overall_weighted, orient="index"
            ).reset_index()
            tokens_df.columns = ["word", "count"]
            tokens_df = tokens_df.sort_values(by=["count"], ascending=False)

            centroids_top_words[i] = tokens_df.head(num_words)

    return centroids_top_words


def print_centroid_top_words(ce_object, centroid_num=None, num_words=5):
    """
    centroid : the centroid for which the top words will be obtained. If none, print for all centroids.
    num_words : number of words to return
    """
    centroids_top_words = rank_words_near_centroid(ce_object)
    if centroid_num:
        print(
            f"For centroid {centroid_num}, the {num_words} most frequently occurring words:"
        )

        display(centroids_top_words[centroid_num].head(num_words))

    else:
        for num in range(ce_object.num_clusters):
            print(
                f"For centroid {num}, the {num_words} most frequently occurring words:"
            )
            display(centroids_top_words[num].head(num_words))


############################################################
# BGM utils
############################################################


def BGM_search_k(
    ce_object,
    n_components_lim,
    use_decomposed=False,
    additional_params={"random_state": 0},
    cluster_with=False,
):
    """
    Fits BGM models with up to n_components_lim and returns visualizations of the results.
    Sets modal number of actual components in self.BGM_modal_k and optionally runs cluster_embeddings() with modal k.

    @param additional_params {param_name: value} of parameters accepted by the sklearn clustering function.
        Cannot include n_components.
    @param cluster_with if True, automatically runs cluster_embeddings with the modal k
    """
    ks = np.linspace(2, n_components_lim, n_components_lim - 1)

    vec = (
        ce_object.decomposed_embedding_representation
        if use_decomposed
        else ce_object.embedding_representation
    )

    bgms = [
        BayesianGaussianMixture(n_components=int(k), **additional_params) for k in ks
    ]

    results = []

    for bgm in bgms:
        bgm.fit(vec)
        results.append((bgm.n_components, len(set(bgm.predict(vec)))))

    results = pd.DataFrame(results)
    results.columns = ["max_n_components", "actual_n_components"]

    BGM_modal_k = mode(results["actual_n_components"]).mode[0]

    if cluster_with:
        clustered = ce_object.cluster_embeddings(
            cluster_method="BayesGaussMix",
            num_clusters=BGM_modal_k,
            use_decomposed=use_decomposed,
            additional_params=additional_params,
        )

    fig, (plt1, plt2) = plt.subplots(ncols=2, figsize=(14, 6))
    results.plot.scatter(
        y="actual_n_components",
        x="max_n_components",
        ax=plt1,
        title="Actual components selected when n max components allowed",
        xticks=range(max(results.max_n_components)),
        yticks=range(max(results.actual_n_components)),
    )
    results.plot.hist(
        y="actual_n_components",
        ax=plt2,
        title="Histogram of components selected",
        xticks=range(max(results.actual_n_components)),
    )

    return BGM_modal_k


############################################################
# Utils
############################################################


def compare_decomp_methods(transformed_embedding, decomp_dims=2):
    """
    Decompose and plot labeled data with each of the decomp methods.
    """
    if decomp_dims == 2:
        for cluster_method in ["PCA", "UMAP", "TSNE"]:
            transformed_embedding.decompose_embeddings(cluster_method, decomp_dims)
            transformed_embedding.plot_labeled_data()
    else:
        print("Decomp dimensions must == 2 to plot.")


def compare_silhouettes(
    transformed_embedding, cluster_method, cluster_values, use_decomposed
):
    """
    Comparing silhouette scores across
    multiple ContextEmbeddings objects
    """

    if use_decomposed:
        vecs_df = transformed_embedding.decomposed_embedding_representation
    else:
        vecs_df = transformed_embedding.embedding_representation

    clustered_with_ks = [copy.copy(transformed_embedding) for i in cluster_values]
    for i, ncluster in enumerate(cluster_values):
        clustered_with_ks[i].cluster_embeddings(
            cluster_method, ncluster, use_decomposed
        )
        clustered_with_ks[i].compute_silhouette(use_decomposed)

    print([item.mean_silhouette_score for item in clustered_with_ks])

    results = pd.DataFrame(
        {
            "num_clusters": cluster_values,
            "mean_silhouette": [
                item.mean_silhouette_score for item in clustered_with_ks
            ],
        }
    )

    results.plot(x="num_clusters", y="mean_silhouette", figsize=(8, 10))
    plt.suptitle(f"Comparison of Silhouette Scores for k= {cluster_values}")
    plt.title(f"Clustered with {cluster_method}")

    print(results)

    return clustered_with_ks
