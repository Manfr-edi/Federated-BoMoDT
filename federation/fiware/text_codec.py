"""Lossless descriptive text transport for Orion-LD 1.5.1's NGSI-v2 parser.

That image rejects apostrophes even in TextUnrestricted values. UTF-8 hex uses
only permitted characters and can also be decoded by PostgreSQL in Grafana.
Identifiers, counts, dates and the source observation are never encoded.
"""


def encode_road_name(value):
    return value.encode('utf-8').hex()


def decode_road_name(attributes):
    encoded=attributes.get('roadNameHex',attributes.get('roadnamehex'))
    if encoded is not None:
        return bytes.fromhex(encoded).decode('utf-8')
    # Read older histories without rewriting their registered devices or data.
    return attributes.get('roadName',attributes.get('roadname'))
