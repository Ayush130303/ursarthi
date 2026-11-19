"""Route ordering.

* n <= 12  : exact Held–Karp dynamic programming, O(n^2 * 2^n)
* n  > 12  : nearest-neighbour construction + 2-opt local search

The previous brute-force permutation search was O(n!) – 10 stops meant
362,880 routes; Held–Karp solves 12 stops in milliseconds.
Index 0 is always the start (current location).
"""
from __future__ import annotations

import math

INF = math.inf


def _cost(m, a, b) -> float:
    v = m[a][b]
    return INF if v is None else float(v)


def route_length(m, route: list[int], return_to_start: bool = False) -> float:
    total = sum(_cost(m, route[i], route[i + 1]) for i in range(len(route) - 1))
    if return_to_start and len(route) > 1:
        total += _cost(m, route[-1], route[0])
    return total


def held_karp(m, return_to_start: bool = False) -> tuple[list[int], float]:
    n = len(m)
    full = 1 << (n - 1)  # subsets of nodes 1..n-1
    dp = [[INF] * n for _ in range(full)]
    parent = [[-1] * n for _ in range(full)]
    for j in range(1, n):
        dp[1 << (j - 1)][j] = _cost(m, 0, j)
    for mask in range(1, full):
        for j in range(1, n):
            bit = 1 << (j - 1)
            if not mask & bit or dp[mask][j] == INF:
                continue
            for k in range(1, n):
                kbit = 1 << (k - 1)
                if mask & kbit:
                    continue
                cand = dp[mask][j] + _cost(m, j, k)
                if cand < dp[mask | kbit][k]:
                    dp[mask | kbit][k] = cand
                    parent[mask | kbit][k] = j
    last_mask = full - 1
    best, best_end = INF, -1
    for j in range(1, n):
        cost = dp[last_mask][j] + (_cost(m, j, 0) if return_to_start else 0)
        if cost < best:
            best, best_end = cost, j
    if best_end == -1 or best == INF:
        raise ValueError("No valid route found. One or more locations are unreachable.")
    route, mask, node = [], last_mask, best_end
    while node != -1:
        route.append(node)
        prev = parent[mask][node]
        mask ^= 1 << (node - 1)
        node = prev
    route.append(0)
    route.reverse()
    return route, best


def nearest_neighbour_2opt(m, return_to_start: bool = False) -> tuple[list[int], float]:
    n = len(m)
    route, unvisited = [0], set(range(1, n))
    while unvisited:
        nxt = min(unvisited, key=lambda j: _cost(m, route[-1], j))
        route.append(nxt)
        unvisited.remove(nxt)
    improved = True
    best = route_length(m, route, return_to_start)
    while improved:
        improved = False
        for i in range(1, n - 1):
            for k in range(i + 1, n):
                cand = route[:i] + route[i:k + 1][::-1] + route[k + 1:]
                length = route_length(m, cand, return_to_start)
                if length + 1e-9 < best:
                    route, best, improved = cand, length, True
    if best == INF:
        raise ValueError("No valid route found. One or more locations are unreachable.")
    return route, best


def solve(distance_matrix, return_to_start: bool = False) -> tuple[list[int], float]:
    n = len(distance_matrix)
    if n <= 1:
        return [0], 0.0
    if n <= 12:
        return held_karp(distance_matrix, return_to_start)
    return nearest_neighbour_2opt(distance_matrix, return_to_start)
