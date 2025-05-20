from datetime import timedelta, timezone, datetime
from datetime import timedelta
from typing import List, Dict, Any
from typing import Optional

from pymongo.errors import BulkWriteError

from modules.db import get_collection
from modules.models.collection_types import Collection as ColEnum


def save(items: List[Dict[str, Any]]) -> None:
    """
    Insert multiple market items into the live collection,
    stamping each with the same UTC timestamp.
    Any duplicates (by hash_code) will be skipped,
    but the rest will succeed.
    """
    if not items:
        return

    ts = datetime.now(timezone.utc)
    for it in items:
        it['timestamp'] = ts

    collection = get_collection(ColEnum.MARKET)
    try:
        # ordered=False => fire off all inserts;
        # duplicate-key errors don’t stop the rest.
        collection.insert_many(items, ordered=False)
    except BulkWriteError as bwe:
        # Optionally inspect bwe.details['writeErrors'] for logging,
        # but you can safely ignore duplicate-key errors here.
        pass



def get_trade_market_item(item_name: str) -> List[Dict[str, Any]]:
    """
    Retrieve all market entries for an item by name.
    """
    cursor = get_collection(ColEnum.MARKET).find(
        {'name': item_name},
        projection={'_id': 0}
    )
    return list(cursor)


def get_trade_market_item_price(
    item_name: str,
    shiny: bool = False,
    tier: Optional[int] = None
) -> Dict[str, Any]:
    """
    Compute price statistics (min, max, avg, mid-80%) for identified and unidentified listings,
    taking each 'amount' into account (so a listing of amount=4 counts as four data points).
    Returns a dict of:
      - lowest_price, highest_price, average_price, average_mid_80_percent_price
      - unidentified_average_price, unidentified_average_mid_80_percent_price
      - total_count, unidentified_count
      - name
    """
    shiny_stat = '$ne' if shiny else '$eq'
    query_filter: Dict[str, Any] = {'name': item_name, 'shiny_stat': {shiny_stat: None}, '$or': [
        {'item_type': {'$in': ['GearItem', 'IngredientItem']}},
        {'item_type': 'MaterialItem', 'tier': tier}
    ]}

    # only add the tier/or-clause if tier was given

    # 2) build pipeline
    pipeline = [
        # 1) filter
        {'$match': query_filter},

        # 2) explode each doc into `amount` copies
        {'$addFields': {'unitIndex': {'$range': [0, '$amount']}}},
        {'$unwind': '$unitIndex'},
        {'$sort': {'listing_price': 1}},
        {'$facet': {
            'identified': [
                {'$match': {'unidentified': {'$ne': True}}},
                {'$group': {
                    '_id': None,
                    'minPrice': {'$min': '$listing_price'},
                    'maxPrice': {'$max': '$listing_price'},
                    'avgPrice': {'$avg': '$listing_price'},
                    'prices': {'$push': '$listing_price'},
                    'count': {'$sum': 1}
                }},
                {'$project': {
                    '_id': 0,
                    'minPrice': {'$round': ['$minPrice', 2]},
                    'maxPrice': {'$round': ['$maxPrice', 2]},
                    'avgPrice': {'$round': ['$avgPrice', 2]},
                    'count': 1,
                    'mid80': {
                        '$cond': [
                            {'$gt': [{'$size': '$prices'}, 2]},
                            {
                                '$slice': [
                                    '$prices',
                                    {'$ceil': {'$multiply': [{'$size': '$prices'}, 0.1]}},
                                    {'$subtract': [
                                        {'$size': '$prices'},
                                        {'$multiply': [
                                            {'$ceil': {'$multiply': [{'$size': '$prices'}, 0.1]}},
                                            2
                                        ]}
                                    ]}
                                ]
                            },
                            '$prices'
                        ]
                    }
                }},
                {'$project': {
                    'minPrice': 1,
                    'maxPrice': 1,
                    'avgPrice': 1,
                    'count': 1,
                    'avgMid80': {'$round': [{'$avg': '$mid80'}, 2]}
                }}
            ],
            'unidentified': [
                {'$match': {'unidentified': True}},
                {'$group': {
                    '_id': None,
                    'avgPrice': {'$avg': '$listing_price'},
                    'prices': {'$push': '$listing_price'},
                    'count': {'$sum': 1}
                }},
                {'$project': {
                    '_id': 0,
                    'avgPrice': {'$round': ['$avgPrice', 2]},
                    'count': 1,
                    'mid80': {
                        '$cond': [
                            {'$gt': [{'$size': '$prices'}, 2]},
                            {
                                '$slice': [
                                    '$prices',
                                    {'$ceil': {'$multiply': [{'$size': '$prices'}, 0.1]}},
                                    {'$subtract': [
                                        {'$size': '$prices'},
                                        {'$multiply': [
                                            {'$ceil': {'$multiply': [{'$size': '$prices'}, 0.1]}},
                                            2
                                        ]}
                                    ]}
                                ]
                            },
                            '$prices'
                        ]
                    }
                }},
                {'$project': {
                    'avgPrice': 1,
                    'count': 1,
                    'avgMid80': {'$round': [{'$avg': '$mid80'}, 2]}
                }}
            ]
        }},

        # 5) stitch facets back into one document, defaulting missing values to 0
        {
            '$project': {
                'lowest_price': {'$ifNull': [{'$arrayElemAt': ['$identified.minPrice', 0]}, 0]},
                'highest_price': {'$ifNull': [{'$arrayElemAt': ['$identified.maxPrice', 0]}, 0]},
                'average_price': {'$ifNull': [{'$arrayElemAt': ['$identified.avgPrice', 0]}, 0]},
                'average_mid_80_percent_price':
                    {'$ifNull': [{'$arrayElemAt': ['$identified.avgMid80', 0]}, 0]},

                'total_count': {
                    '$add': [
                        {'$ifNull': [{'$arrayElemAt': ['$identified.count', 0]}, 0]},
                        {'$ifNull': [{'$arrayElemAt': ['$unidentified.count', 0]}, 0]}
                    ]
                },

                'unidentified_average_price':
                    {'$ifNull': [{'$arrayElemAt': ['$unidentified.avgPrice', 0]}, 0]},
                'unidentified_average_mid_80_percent_price':
                    {'$ifNull': [{'$arrayElemAt': ['$unidentified.avgMid80', 0]}, 0]},
                'unidentified_count':
                    {'$ifNull': [{'$arrayElemAt': ['$unidentified.count', 0]}, 0]}
            }
        },
        {'$match': {
            'total_count': {'$gt': 0}
        }}
    ]

    cursor = get_collection(ColEnum.MARKET).aggregate(pipeline)
    try:
        stats = cursor.next()
    except StopIteration:
        return {}

    stats['name'] = item_name
    return stats


def get_price_history(
        item_name: str,
        shiny: bool = False,
        days: int = 14,
        tier: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Retrieve the price history of an item over the past `days` days.
    """
    shiny_stat = '$ne' if shiny else '$eq'
    query_filter: Dict[str, Any] = {
        'name': item_name,
        'shiny_stat': {shiny_stat: None}
    }
    if tier is not None:
        query_filter['$or'] = [
            {'item_type': {'$in': ['GearItem', 'IngredientItem']}},
            {'item_type': 'MaterialItem', 'tier': tier}
        ]
    start_date = datetime.now(timezone.utc) - timedelta(days=days + 8)
    query_filter['date'] = {'$gte': start_date}

    cursor = get_collection(ColEnum.MARKET_ARCHIVE).find(
        filter=query_filter,
        sort=[('date', 1)],
        projection={'_id': 0}
    )
    return list(cursor)


def get_latest_price_history(
        item_name: str,
        shiny: bool = False,
        tier: Optional[int] = None,
        days: int = 7,
) -> Dict[str, Any]:
    """
    Aggregate the last N documents (default 7) to compute averages.
    """
    shiny_stat = '$ne' if shiny else '$eq'
    query_filter: Dict[str, Any] = {
        'name': item_name,
        'shiny_stat': {shiny_stat: None}
    }
    if tier is not None:
        query_filter['$or'] = [
            {'item_type': {'$in': ['GearItem', 'IngredientItem']}},
            {'item_type': 'MaterialItem', 'tier': tier}
        ]
    cursor = get_collection(ColEnum.MARKET_ARCHIVE).find(
        filter=query_filter,
        sort=[('date', -1)],
        projection={'_id': 0}
    ).limit(days)
    docs = list(cursor)
    stats: Dict[str, Any] = {}
    if docs:
        fields = [
            'lowest_price', 'highest_price', 'average_price',
            'total_count', 'average_mid_80_percent_price',
            'unidentified_average_price', 'unidentified_average_mid_80_percent_price',
            'unidentified_count'
        ]
        for f in fields:
            vals = [d.get(f) for d in docs if d.get(f) is not None]
            stats[f] = sum(vals) / len(vals) if vals else None
        stats['name'] = item_name
        stats['document_count'] = len(docs)
    return stats


def get_all_items_ranking() -> List[Dict[str, Any]]:
    """
    Generate a ranking of all items based on archived average price.
    """
    pipeline = [
        {'$group': {
            '_id': '$name',
            'lowest_price': {'$min': '$lowest_price'},
            'highest_price': {'$max': '$highest_price'},
            'average_price': {'$avg': '$average_price'},
            'average_total_count': {'$avg': '$total_count'},
            'average_unidentified_count': {'$avg': '$unidentified_count'},
            'average_mid_80_percent_price': {'$avg': '$average_mid_80_percent_price'},
            'unidentified_average_mid_80_percent_price': {'$avg': '$unidentified_average_mid_80_percent_price'}
        }},
        {'$match': {
            'average_mid_80_percent_price': {'$gte': 20480},
            'average_total_count': {'$gte': 2}
        }},
        {'$sort': {'average_price': -1}}
    ]
    cursor = get_collection(ColEnum.MARKET_ARCHIVE).aggregate(pipeline)
    return [
        dict(name=doc['_id'], **{k: doc[k] for k in doc if k != '_id'})
        for doc in cursor
    ]
