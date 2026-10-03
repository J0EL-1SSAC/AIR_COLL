from pyproj import Transformer


def test_airport_origin_maps_to_local_frame_origin():
    lat, lon = 12.9941, 80.1709
    transform = Transformer.from_crs(
        "EPSG:4326",
        f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m +no_defs",
        always_xy=True,
    )
    x_m, y_m = transform.transform(lon, lat)
    assert abs(x_m) < 0.01
    assert abs(y_m) < 0.01
