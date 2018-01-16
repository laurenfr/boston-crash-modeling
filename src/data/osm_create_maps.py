import argparse
import util
import osmnx as ox
from shapely.geometry import MultiLineString, Point
import fiona
import shutil
import os
import re

PROCESSED_FP = None
MAP_FP = None


def get_city(city):

    # Not used currently; once this script is finished, probably delete
    gdf_place = ox.gdf_from_place(city)
    polygon = gdf_place['geometry'].unary_union
    city_info = ox.core.osm_net_download(polygon)
    elements = city_info[0]['elements']

    return elements


def simple_get_roads(city):

    G = ox.graph_from_place(city, network_type='drive', simplify=True)
    # osmnx creates a directory for the nodes and edges
    ox.save_graph_shapefile(
        G, filename='temp', folder=MAP_FP)
    # Copy and remove temp directory
    tempdir = MAP_FP + '/temp/'
    for filename in os.listdir(tempdir + 'edges/'):
        name, extension = filename.split('.')
        shutil.move(
            tempdir + 'edges/' + filename, MAP_FP + 'osm_ways.' + extension)
    for filename in os.listdir(tempdir + 'nodes/'):
        name, extension = filename.split('.')
        shutil.move(
            tempdir + 'nodes/' + filename, MAP_FP + 'osm_nodes.' + extension)
    shutil.rmtree(tempdir)


def get_roads(elements):

    # Not used currently; once this script is finished, probably delete

    # Turn the nodes into key value pairs where key is the node id
    nodes = {x['id']: x for x in elements if x['type'] == 'node'}

    node_info = {}
    dup_nodes = {}
    # Any node that's shared by more than one way is an intersection
    non_named_count = 0
    way_lines = []
    unnamed_lines = []
    service_lines = []
    road_types = {}
    for way in elements:
        if way['type'] == 'way':

            coords = []
            way_nodes = way['nodes']
    
            prev = None
            for i in range(len(way_nodes)):
                n = way_nodes[i]

                if prev:
                    coords.append((
                        (prev['lon'], prev['lat']),
                        (nodes[n]['lon'], nodes[n]['lat']),
                    ))
                prev = nodes[n]

                if n in node_info.keys():
                    dup_nodes[n] = 1
                    node_info[n]['count'] += 1
                    node_info[n]['ways'].append(way['id'])
                else:
                    node_info[n] = {'count': 1, 'ways': []}

            tags = way['tags']
            if tags['highway'] not in road_types.keys():
                road_types[tags['highway']] = 0
            road_types[tags['highway']] += 1

            name = tags['name'] if 'name' in tags else ''

            # Ignore footways and service roads in the main map
            if tags['highway'] in ('service', 'footway'):
                service_lines.append((
                    MultiLineString(coords), {
                        'name': name,
                        'id': way['id']
                    }))
            else:
                # if it's unnamed AND not residential, don't include
                # in main list
                if not name and tags['highway'] != 'residential':
                    unnamed_lines.append((
                        MultiLineString(coords), {'id': way['id']}))
                    non_named_count += 1
                else:
                    oneway = tags['oneway'] if 'oneway' in tags else None
                    width = tags['width'] if 'width' in tags else None
                    lanes = tags['lanes'] if 'lanes' in tags else None
                    ma_way_id = tags['massgis:way_id'] \
                        if 'massgis:way_id' in tags else None
                    way_lines.append((
                        MultiLineString(coords), {
                            'name': name,
                            'id': way['id'],
                            'width': width,
                            'type': tags['highway'],
                            'lanes': lanes,
                            'oneway': oneway,
                            'ma_way_id': ma_way_id
                        }))

    print 'Found ' + str(len(way_lines)) + ' residential roads'
    print 'Found ' + str(len(unnamed_lines)) + ' unnamed roads'
    print 'Found ' + str(len(service_lines)) + ' service roads or footpaths'
    print "Found " + str(len(dup_nodes.keys())) + " intersections"

    # Output the points that are duplicates with each other
    # This means by some definition they are intersections
    points = []
    for node in dup_nodes.keys():
        points.append((
            Point(nodes[node]['lon'], nodes[node]['lat']),
            {
                'node_id': node,
                'count': node_info[node]['count'],
            }
        ))

    schema = {'geometry': 'Point', 'properties': {
        'node_id': 'int',
        'count': 'int',
    }}
    util.write_points(
        points,
        schema,
        MAP_FP + '/osm_inter.shp')

    print road_types
    return way_lines, service_lines, unnamed_lines


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("city", help="e.g. 'Boston, Massachusetts, USA'")

    # Right now, you must also give data directory
    parser.add_argument("datadir", type=str,
                        help="data directory")

    args = parser.parse_args()
    city = args.city
    PROCESSED_FP = args.datadir + '/processed/'
    MAP_FP = args.datadir + '/processed/maps/'

    # If maps do not exist, create
    if not os.path.exists(MAP_FP + '/osm_ways.shp'):
        print 'Generating maps from open street map'
        simple_get_roads(city)

    if not os.path.exists(MAP_FP + '/osm_ways_3857.shp'):
        way_results = fiona.open(MAP_FP + '/osm_ways.shp')

        # Convert the map from above to 3857
        reprojected_way_lines = [
            tuple(
                x.values()
            ) for x in util.reproject_records(way_results)]

        # Add values to schema if they don't exist, so new map won't break
        # Eventually do something better to handle differing schema elements
        # Probably should map osm maps to CoB data
        # Use speed limit if given in osm
        for way_line in reprojected_way_lines:
            speed = way_line[1]['maxspeed']
            if speed:
                # It's possible that this combines two segments and thus speeds
                # doesn't happen a ton so just choose first one
                speed = re.search('[0-9]+', speed).group(0)
            else:
                speed = 0

            way_line[1].update({
                'AADT': 0,
                'SPEEDLIMIT': speed,
                'Struct_Cnd': 0,
                'Surface_Tp': 0,
                'F_F_Class': 0,
            })
        schema = way_results.schema
        schema['properties'].update({
            'AADT': 'int',
            'SPEEDLIMIT': 'int',
            'Struct_Cnd': 'int',
            'Surface_Tp': 'int',
            'F_F_Class': 'int',
        })

        util.write_shp(
            schema,
            MAP_FP + '/osm_ways_3857.shp',
            reprojected_way_lines, 0, 1, crs=fiona.crs.from_epsg(3857))
