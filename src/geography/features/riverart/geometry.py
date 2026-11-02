import heapq
import math

import geopandas as gpd
import numpy as np
from shapely.geometry import LineString, MultiPoint, Point

from geography.features.spatial import CRS

BRIDGES = (0.05, 0.15, 0.4, 1.0, 2.5)
BRIDGE_COST = 3
MIN_PIECE_SHARE = 0.08
SIDE_CHANNEL_DEVIATION = 0.02
SIDE_CHANNEL_REJOIN = 0.06
LAKE_MARGIN = 0.03
STUB_MARGIN = 0.01
MAX_STUB_SHARE = 0.25

Vertex = tuple[float, float]
Graph = dict[Vertex, dict[Vertex, float]]


def vertex_of(coordinate) -> Vertex:
    return round(coordinate[0], 3), round(coordinate[1], 3)


def pieces_of(geometry: gpd.GeoSeries) -> list[LineString]:
    return [line for line in geometry.explode(index_parts=False) if len(line.coords) > 1]


def graph_of(geometry: gpd.GeoSeries, bridge: float) -> Graph:
    segments = [[vertex_of(c) for c in line.coords] for line in pieces_of(geometry)]
    graph: Graph = {}

    def link(a: Vertex, b: Vertex, cost: float) -> None:
        graph.setdefault(a, {})
        graph.setdefault(b, {})
        graph[a][b] = graph[b][a] = min(cost, graph[a].get(b, math.inf))

    for segment in segments:
        for a, b in zip(segment[:-1], segment[1:]):
            if a != b:
                link(a, b, math.dist(a, b))
    ends = [end for segment in segments for end in (segment[0], segment[-1])]
    distances = np.linalg.norm(np.array(ends)[:, None, :] - np.array(ends)[None, :, :], axis=2)
    for i, j in zip(*np.where((distances < bridge) & (distances > 0))):
        if i < j:
            link(ends[i], ends[j], float(distances[i, j]) * BRIDGE_COST)
    return graph


def shortest(graph: Graph, start: Vertex, goal: Vertex | None = None) -> tuple[dict[Vertex, float], dict[Vertex, Vertex]]:
    best, previous, queue = {start: 0.0}, {}, [(0.0, start)]
    while queue:
        cost, node = heapq.heappop(queue)
        if node == goal:
            break
        if cost > best.get(node, math.inf):
            continue
        for other, weight in graph[node].items():
            if cost + weight < best.get(other, math.inf):
                best[other], previous[other] = cost + weight, node
                heapq.heappush(queue, (cost + weight, other))
    return best, previous


def furthest(best: dict[Vertex, float]) -> Vertex:
    return max(best, key=best.get)


def river_ends(geometry: gpd.GeoSeries) -> tuple[Point, Point]:
    for bridge in BRIDGES:
        graph = graph_of(geometry, bridge)
        reached, _ = shortest(graph, next(iter(graph)))
        if len(reached) < len(graph):
            continue
        first = furthest(reached)
        second = furthest(shortest(graph, first)[0])
        return Point(first), Point(second)
    return hull_ends(geometry)


def hull_ends(geometry: gpd.GeoSeries) -> tuple[Point, Point]:
    points = [Point(c) for line in pieces_of(geometry) for c in line.coords]
    hull = list(MultiPoint(points).convex_hull.exterior.coords) if len(points) > 3 else [(p.x, p.y) for p in points]
    return max(((Point(a), Point(b)) for a in hull for b in hull), key=lambda pair: pair[0].distance(pair[1]))


def ordered_path(geometry: gpd.GeoSeries, source: Point, mouth: Point) -> LineString:
    for bridge in BRIDGES:
        graph = graph_of(geometry, bridge)
        nearest = lambda point: min(graph, key=lambda node: math.dist(node, (point.x, point.y)))
        start, goal = nearest(source), nearest(mouth)
        _, previous = shortest(graph, start, goal)
        if goal in previous or start == goal:
            path = [goal]
            while path[-1] != start:
                path.append(previous[path[-1]])
            return LineString(path[::-1])
    return max(pieces_of(geometry), key=lambda line: line.length)


def main_pieces(river: gpd.GeoSeries) -> gpd.GeoSeries:
    pieces = pieces_of(river)
    longest = max(piece.length for piece in pieces)
    return gpd.GeoSeries([piece for piece in pieces if piece.length >= MIN_PIECE_SHARE * longest], crs=CRS)


def without_side_channels(river: gpd.GeoSeries, path: LineString) -> gpd.GeoSeries:
    def rejoins(line: LineString) -> bool:
        middle = Point(line.coords[len(line.coords) // 2])
        return (
            middle.distance(path) > SIDE_CHANNEL_DEVIATION
            and Point(line.coords[0]).distance(path) < SIDE_CHANNEL_REJOIN
            and Point(line.coords[-1]).distance(path) < SIDE_CHANNEL_REJOIN
        )

    kept = [line for line in pieces_of(river) if not rejoins(line)]
    return gpd.GeoSeries([line for line in kept if line.length > 0], crs=CRS)


def lake_exit(path: LineString, lake: gpd.GeoSeries | None, near: Point | None) -> int | None:
    coordinates = list(path.coords)
    if lake is not None:
        inside = lake.union_all().buffer(LAKE_MARGIN)
        flags = [inside.contains(Point(c)) for c in coordinates]
        if flags and flags[0]:
            return next((i for i, flag in enumerate(flags) if not flag), len(flags)) - 1
        return next((i for i, flag in enumerate(flags) if flag), None)
    if near is None:
        return None
    return min(range(len(coordinates)), key=lambda i: math.dist(coordinates[i], (near.x, near.y)))


def trimmed_at_lake(river: gpd.GeoSeries, path: LineString, exit_index: int | None) -> tuple[gpd.GeoSeries, LineString] | None:
    if not exit_index:
        return None
    coordinates = list(path.coords)
    stub = LineString(coordinates[: exit_index + 1])
    if stub.length > MAX_STUB_SHARE * path.length:
        return None
    rest = river.difference(stub.buffer(STUB_MARGIN)).explode(index_parts=False)
    return gpd.GeoSeries([line for line in rest if not line.is_empty], crs=CRS), LineString(coordinates[exit_index:])
