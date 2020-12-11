from sacred import Experiment
from sacred.observers import MongoObserver
import numpy as np
from random import sample
import torch
import os


def create_experiment(name='exp', database=None):
    """ Create Scared experiment object for experiment logging """
    ex = Experiment(name)

    atlas_user = os.environ.get('MONGO_DB_USER')
    atlas_password = os.environ.get('MONGO_DB_PASS')
    atlas_host = os.environ.get('MONGO_DB_HOST')

    # Add remote MongoDB observer, only if environment variables are set
    if atlas_user and atlas_password and atlas_host:
        ex.observers.append(MongoObserver(
            url=f"mongodb+srv://{atlas_user}:{atlas_password}@{atlas_host}",
            db_name=database))
    return ex

#######################################################################################################################
# Relation Prediction Utils
#######################################################################################################################

def select_sampling(method):
    method = method.lower()
    if method == 'uniform':
        return uniform_sampling
    elif method == 'edge-neighborhood':
        return edge_neighborhood
    else:
        raise NotImplementedError(f'{method} sampling method has not been implemented!')

def uniform_sampling(graph, sample_size=30000, entities=None, train_triplets=None):
    """Random uniform sampling"""
    return sample(graph, sample_size)

def edge_neighborhood(train_triples, sample_size=30000, entities=None):
    """Edge neighborhood sampling"""

    # TODO: Clean this up
    entities = {v: k for k, v in entities.items()}
    adj_list = [[] for _ in entities]
    for i, triplet in enumerate(train_triples):
        adj_list[triplet[0]].append([i, triplet[2]])
        adj_list[triplet[2]].append([i, triplet[0]])

    degrees = np.array([len(a) for a in adj_list])
    adj_list = [np.array(a) for a in adj_list]

    edges = np.zeros((sample_size), dtype=np.int32)

    sample_counts = np.array([d for d in degrees])
    picked = np.array([False for _ in train_triples])
    seen = np.array([False for _ in degrees])

    for i in range(0, sample_size):
        weights = sample_counts * seen

        if np.sum(weights) == 0:
            weights = np.ones_like(weights)
            weights[np.where(sample_counts == 0)] = 0

        probabilities = (weights) / np.sum(weights)
        chosen_vertex = np.random.choice(np.arange(degrees.shape[0]), p=probabilities)
        chosen_adj_list = adj_list[chosen_vertex]
        seen[chosen_vertex] = True

        chosen_edge = np.random.choice(np.arange(chosen_adj_list.shape[0]))
        chosen_edge = chosen_adj_list[chosen_edge]
        edge_number = chosen_edge[0]

        while picked[edge_number]:
            chosen_edge = np.random.choice(np.arange(chosen_adj_list.shape[0]))
            chosen_edge = chosen_adj_list[chosen_edge]
            edge_number = chosen_edge[0]

        edges[i] = edge_number
        other_vertex = chosen_edge[1]
        picked[edge_number] = True
        sample_counts[chosen_vertex] -= 1
        sample_counts[other_vertex] -= 1
        seen[other_vertex] = True

    edges = [train_triples[e] for e in edges]

    return edges

def corrupt(batch, num_nodes, head_corrupt_prob, device='cpu'):
    """Corrupts the negatives of a batch of triples. Randomly corrupts either heads or tails."""
    bs, ns, _ = batch.size()

    # new entities to insert
    corruptions = torch.randint(size=(bs * ns,),low=0, high=num_nodes, dtype=torch.long, device=device)

    # boolean mask for entries to corrupt
    mask = torch.bernoulli(torch.empty(
        size=(bs, ns, 1), dtype=torch.float, device=device).fill_(head_corrupt_prob)).to(torch.bool)
    zeros = torch.zeros(size=(bs, ns, 1), dtype=torch.bool, device=device)
    mask = torch.cat([mask, zeros, ~mask], dim=2)

    batch[mask] = corruptions

    return batch.view(bs * ns, -1)

def negative_sampling(positive_triples, entity_dictionary, neg_sample_rate):
    """ Generates a set of negative samples by corrupting triples """

    all_triples = np.array(positive_triples)
    s = np.resize(all_triples[:, 0], (len(positive_triples)*neg_sample_rate,))
    p = np.resize(all_triples[:, 1], (len(positive_triples)*neg_sample_rate,))
    o = np.random.randint(low=0, high=len(entity_dictionary), size=(len(positive_triples)*neg_sample_rate,))
    negative_triples = np.stack([s, p, o], axis=1)

    return negative_triples.tolist()

def corrupt_heads(entity_dictionary, p, o):
    """ Generate a list of candidate triples by replacing the head with every entity for each test triplet """
    return [(s, p, o) for s in range(len(entity_dictionary))]

def corrupt_tails(s, p, entity_dictionary):
    """ Generate a list of candidate triples by replacing the tail with every entity for each test triplet """
    return [(s, p, o) for o in range(len(entity_dictionary))]

def filter_triples(candidate_triples, all_triples, correct_triple):
    """ Filter out candidate_triples that are present in all_triples, but keep correct_triple """
    return [triple for triple in set(candidate_triples) if not triple in all_triples or triple == correct_triple]

def compute_mrr(rank):
    """ Compute Mean Reciprocal Rank for a given list of ranked triples """
    return 1.0/rank

def compute_hits(rank, k):
    """ Compute Precision at K for a given list of ranked triples """
    if k == 1:
        return 1 if rank == k else 0
    else:
        return 1 if rank <= k else 0

def rank_triple(scores, candidate_triples, correct_triple):
    """ Finds rank of the correct triple after sorting candidates by their scores """
    sorted_candidates = [tuple(i[0]) for i in sorted(zip(candidate_triples.tolist(), scores.tolist()), key=lambda i: -i[1])]
    rank = sorted_candidates.index(correct_triple) + 1
    return rank

def compute_metrics(scores, candidates, correct_triple, k=None):
    """ Returns MRR and Hits@k (k=1,3,10) values for a given triple prediction """
    if k is None:
        k = [1, 3, 10]

    rank = rank_triple(scores, candidates, correct_triple)
    mrr = compute_mrr(rank)
    hits_at_k = { i:compute_hits(rank, i) for i in k }

    return mrr, hits_at_k
