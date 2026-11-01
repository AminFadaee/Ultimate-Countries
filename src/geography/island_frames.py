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
    centred_m: float | None = None
    lenses: tuple[tuple[float, float, float], ...] = ()


ISLAND_FRAMES = {
    "AIA": IslandFrame(whole=True),
    "ALA": IslandFrame(whole=True, marker_only=True),
    "ASM": IslandFrame(anchor="AUS", whole=True, zoom_out=1.08),
    "ATG": IslandFrame(whole=True),
    "BES": IslandFrame(whole=True, lenses=((-68.27, 12.2, 25), (-63.23, 17.63, 8), (-62.97, 17.49, 10))),
    "BHR": IslandFrame(whole=True),
    "CCK": IslandFrame(whole=True, centred_m=2_500_000, lenses=((96.87, -12.12, 15),)),
    "COK": IslandFrame(anchor="NZL", focus=(-159.8, -21.2), zoom_out=1.5, lenses=((-159.78, -21.23, 30), (-158.1, -20.0, 60))),
    "COM": IslandFrame(anchor="MDG", whole=True),
    "CPV": IslandFrame(whole=True, marker_only=True),
    "CYM": IslandFrame(whole=True),
    "FRO": IslandFrame(whole=True),
    "FSM": IslandFrame(anchor="PNG", focus=(158.2, 6.9), marker_only=True),
    "GLP": IslandFrame(whole=True),
    "GRD": IslandFrame(whole=True),
    "KIR": IslandFrame(anchor="FJI", focus=(173.0, 1.4), marker_only=True, zoom_out=1.8),
    "MDV": IslandFrame(anchor="IND", whole=True, marker_only=True),
    "MHL": IslandFrame(anchor="PNG", whole=True, marker_only=True),
    "MNP": IslandFrame(anchor="JPN", whole=True, zoom_out=1.7, lenses=((145.5, 15.0, 100), (145.8, 18.1, 40))),
    "MUS": IslandFrame(),
    "PCN": IslandFrame(anchor="CHL", focus=(-128.3, -24.4), lenses=((-130.10, -25.07, 15), (-128.32, -24.37, 15))),
    "PLW": IslandFrame(focus=(134.5, 7.4), zoom_out=1.7),
    "PYF": IslandFrame(focus=(-149.5, -17.6), reach_m=400_000, centred_m=2_000_000, lenses=((-149.45, -17.65, 60), (-151.5, -16.6, 90), (-139.9, -9.0, 150))),
    "SHN": IslandFrame(focus=(-5.72, -15.95), centred_m=3_000_000, lenses=((-5.72, -15.95, 20), (-14.37, -7.95, 15), (-12.28, -37.10, 25))),
    "STP": IslandFrame(whole=True, marker_only=True),
    "SYC": IslandFrame(anchor="TZA", anchor_reach_m=2_100_000, zoom_out=1.3, marker_only=True),
    "TCA": IslandFrame(whole=True),
    "TKL": IslandFrame(anchor="WSM", focus=(-171.8, -9.2), reach_m=200_000, marker_only=True, zoom_out=1.8),
    "TON": IslandFrame(anchor="AUS", whole=True, marker_only=True, zoom_out=1.08),
    "TUV": IslandFrame(anchor="AUS", whole=True, marker_only=True),
    "VCT": IslandFrame(whole=True, zoom_out=1.7),
    "VGB": IslandFrame(whole=True),
    "VIR": IslandFrame(whole=True),
    "WLF": IslandFrame(anchor="FJI", whole=True, zoom_out=1.35, lenses=((-176.17, -13.29, 20), (-178.15, -14.28, 25))),
    "WSM": IslandFrame(anchor="AUS", whole=True, zoom_out=1.08),
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


def lens_subjects(base: gpd.GeoSeries, frame: IslandFrame) -> tuple[gpd.GeoSeries, ...]:
    clusters = (cluster_of(base, IslandFrame(focus=(longitude, latitude), reach_m=kilometres * 1000)) for longitude, latitude, kilometres in frame.lenses)
    return tuple(grown_to_dots(cluster, frame.dot_share) for cluster in clusters if not cluster.empty)


def disc_around(home: gpd.GeoSeries, metres: float, crs) -> gpd.GeoSeries:
    local = local_projection(home)
    return gpd.GeoSeries([home.to_crs(local).union_all().buffer(metres)], crs=local).to_crs(crs)


def island_scene(scene: Scene, country: Country, data: NaturalEarth, frame: IslandFrame) -> Scene:
    base = scene.lens_subject
    subject = base if frame.whole else cluster_of(base, frame)
    scene = replace(scene, lens_highlight=grown_to_dots(subject, frame.dot_share), lenses=lens_subjects(base, frame), zoom_threshold=1.0, marker_only=frame.marker_only, frame_scale=frame.zoom_out)
    if not frame.anchor and not frame.centred_m:
        return scene
    home = country.core.geometry if frame.whole else cluster_of(country.core.geometry, frame)
    if frame.centred_m:
        return replace(scene, frame=disc_around(home, frame.centred_m, data.crs), framing=Framing.CONTEXT)
    anchor = anchor_near(home, get_mainland(data.country(frame.anchor).core.geometry), frame.anchor_reach_m)
    return replace(scene, frame=gpd.GeoSeries(pd.concat([home, anchor]).values, crs=data.crs), framing=Framing.CONTEXT)
