import math
from dataclasses import dataclass, replace

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from geography.data import Country, NaturalEarth
from geography.features.maps import local_projection
from geography.render import EQUAL_AREA, LENS_PADDING, NEARBY_PART_DISTANCE, Framing, Scene, get_mainland

DOT_SHARE = 0.006


@dataclass(frozen=True)
class IslandFrame:
    anchor: str | None = None
    anchor_reach_m: float | None = None
    whole: bool = False
    focus: tuple[float, float] | None = None
    reach_m: float = NEARBY_PART_DISTANCE
    dot_share: float = DOT_SHARE
    marker_only: bool = False
    zoom_out: float = 1.0


ISLAND_FRAMES = {
    "ATG": IslandFrame(whole=True),
    "BHR": IslandFrame(whole=True),
    "COM": IslandFrame(anchor="MDG", whole=True),
    "CPV": IslandFrame(whole=True, marker_only=True),
    "GRD": IslandFrame(whole=True),
    "MDV": IslandFrame(anchor="IND", whole=True, marker_only=True),
    "MUS": IslandFrame(),
    "PLW": IslandFrame(focus=(134.5, 7.4), zoom_out=1.7),
    "SYC": IslandFrame(anchor="TZA", anchor_reach_m=2_100_000, zoom_out=1.3, marker_only=True),
    "VCT": IslandFrame(whole=True, zoom_out=1.7),
}


def grown_to_dots(shape: gpd.GeoSeries, share: float) -> gpd.GeoSeries:
    parts = shape.to_crs(local_projection(shape)).explode(index_parts=False)
    minx, miny, maxx, maxy = parts.total_bounds
    smallest = share * LENS_PADDING * max(maxx - minx, maxy - miny)
    growth = (smallest - (parts.area / math.pi) ** 0.5).clip(lower=0)
    return parts.buffer(growth).to_crs(shape.crs)


def cluster_of(shape: gpd.GeoSeries, frame: IslandFrame) -> gpd.GeoSeries:
    parts = shape.explode(index_parts=False).reset_index(drop=True)
    if frame.focus is None:
        centre = get_mainland(parts)
    else:
        spot = gpd.GeoSeries([Point(frame.focus)], crs="EPSG:4326").to_crs(EQUAL_AREA).iloc[0]
        centre = parts.iloc[[parts.to_crs(EQUAL_AREA).distance(spot).idxmin()]]
    local = local_projection(centre)
    return parts[parts.to_crs(local).distance(centre.to_crs(local).iloc[0]) <= frame.reach_m]


def anchor_near(home: gpd.GeoSeries, anchor: gpd.GeoSeries, reach_m: float | None) -> gpd.GeoSeries:
    if reach_m is None:
        return anchor
    local = local_projection(home)
    region = home.to_crs(local).union_all().buffer(reach_m)
    return gpd.GeoSeries([anchor.to_crs(local).union_all().intersection(region)], crs=local).to_crs(anchor.crs)


def island_scene(scene: Scene, country: Country, data: NaturalEarth, frame: IslandFrame) -> Scene:
    subject = scene.lens_subject if frame.whole else cluster_of(scene.lens_subject, frame)
    scene = replace(scene, lens_highlight=grown_to_dots(subject, frame.dot_share), zoom_threshold=1.0, marker_only=frame.marker_only, frame_scale=frame.zoom_out)
    if not frame.anchor:
        return scene
    home = country.core.geometry if frame.whole else cluster_of(country.core.geometry, frame)
    anchor = anchor_near(home, get_mainland(data.country(frame.anchor).core.geometry), frame.anchor_reach_m)
    return replace(scene, frame=gpd.GeoSeries(pd.concat([home, anchor]).values, crs=data.crs), framing=Framing.CONTEXT)
