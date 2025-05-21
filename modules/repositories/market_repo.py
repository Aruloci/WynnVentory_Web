from datetime import timedelta
from datetime import timezone, datetime
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


def get_trade_market_item(
    item_name: str,
    shiny: bool = False,
    tier: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Retrieve all market entries for an item by name.
    """
    shiny_stat = '$ne' if shiny else '$eq'
    query_filter: Dict[str, Any] = {'name': item_name, 'shiny_stat': {shiny_stat: None}, '$or': [
        {'item_type': {'$in': ['GearItem', 'IngredientItem']}},
        {'item_type': 'MaterialItem', 'tier': tier}
    ]}

    cursor = get_collection(ColEnum.MARKET).find(
        filter=query_filter,
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

    # 2) build pipeline
    pipeline = [
        # 1) Only the docs we care about, in price order
        {'$match': query_filter},
        {'$addFields': {'unitIndex': {'$range': [0, '$amount']}}},
        {'$unwind': '$unitIndex'},
        {'$sort': {'listing_price': 1}},

        # 2) Single pass grouping
        {'$group': {
            '_id': None,
            # pull tier & name from the first doc in sort order
            'tier': {'$first': '$tier'},
            'name': {'$first': '$name'},

            # all identified prices in one array
            'identifiedPrices': {
                '$push': {
                    '$cond': [
                        {'$ne': ['$unidentified', True]},
                        '$listing_price',
                        '$$REMOVE'
                    ]
                }
            },
            # all unidentified prices in another
            'unidentifiedPrices': {
                '$push': {
                    '$cond': [
                        {'$eq': ['$unidentified', True]},
                        '$listing_price',
                        '$$REMOVE'
                    ]
                }
            },

            # counts & sums & mins & maxes
            'identifiedCount': {'$sum': {'$cond': [{'$ne': ['$unidentified', True]}, 1, 0]}},
            'unidentifiedCount': {'$sum': {'$cond': [{'$eq': ['$unidentified', True]}, 1, 0]}},
            'identifiedMin': {'$min': {'$cond': [{'$ne': ['$unidentified', True]}, '$listing_price', None]}},
            'identifiedMax': {'$max': {'$cond': [{'$ne': ['$unidentified', True]}, '$listing_price', None]}},
            'identifiedAvg': {'$avg': {'$cond': [{'$ne': ['$unidentified', True]}, '$listing_price', None]}},
            'unidentifiedAvg': {'$avg': {'$cond': [{'$eq': ['$unidentified', True]}, '$listing_price', None]}}
        }},

        # 3) Final projection
        {'$project': {
            'tier': 1,
            'name': 1,

            'lowest_price': {'$round': ['$identifiedMin', 2]},
            'highest_price': {'$round': ['$identifiedMax', 2]},
            'average_price': {'$round': ['$identifiedAvg', 2]},

            'total_count': '$identifiedCount',
            'unidentified_count': '$unidentifiedCount',
            'unidentified_average_price': {'$round': ['$unidentifiedAvg', 2]},

            'average_mid_80_percent_price': {
                '$round': [
                    {
                        '$cond': [
                            {'$gt': [{'$size': '$identifiedPrices'}, 2]},
                            {'$avg': {
                                '$slice': [
                                    '$identifiedPrices',
                                    {'$ceil': {'$multiply': [{'$size': '$identifiedPrices'}, 0.1]}},
                                    {
                                        '$subtract': [
                                            {'$size': '$identifiedPrices'},
                                            {'$multiply': [
                                                2,
                                                {'$ceil': {'$multiply': [{'$size': '$identifiedPrices'}, 0.1]}}
                                            ]}
                                        ]
                                    }
                                ]
                            }},
                            {'$avg': '$identifiedPrices'}
                        ]
                    },
                    2
                ]
            },

            # mid-80-percent for unidentified
            'unidentified_average_mid_80_percent_price': {
                '$round': [
                    {
                        '$cond': [
                            {'$gt': [{'$size': '$unidentifiedPrices'}, 2]},
                            {'$avg': {
                                '$slice': [
                                    '$unidentifiedPrices',
                                    {'$ceil': {'$multiply': [{'$size': '$unidentifiedPrices'}, 0.1]}},
                                    {
                                        '$subtract': [
                                            {'$size': '$unidentifiedPrices'},
                                            {'$multiply': [
                                                2,
                                                {'$ceil': {'$multiply': [{'$size': '$unidentifiedPrices'}, 0.1]}}
                                            ]}
                                        ]
                                    }
                                ]
                            }},
                            {'$avg': '$unidentifiedPrices'}
                        ]
                    },
                    2
                ]
            }
        }}
    ]

    cursor = get_collection(ColEnum.MARKET).aggregate(pipeline)
    try:
        stats = cursor.next()
    except StopIteration:
        return {}

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
    query_filter: Dict[str, Any] = {'name': item_name, 'shiny_stat': {shiny_stat: None}, '$or': [
        {'item_type': {'$in': ['GearItem', 'IngredientItem']}},
        {'item_type': 'MaterialItem', 'tier': tier}
    ]}

    start_date = datetime.now(timezone.utc) - timedelta(days=days + 8)
    query_filter['date'] = {'$gte': start_date}

    cursor = get_collection(ColEnum.MARKET_ARCHIVE).find(
        filter=query_filter,
        sort=[('date', 1)],
        projection={'_id': 0}
    )
    return list(cursor)


def get_historic_average(
        item_name: str,
        shiny: bool = False,
        tier: Optional[int] = None,
        days: int = 7,
) -> Dict[str, Any]:
    """
    Aggregate the last N documents (default 7) to compute averages.
    """
    shiny_stat = '$ne' if shiny else '$eq'
    query_filter: Dict[str, Any] = {'name': item_name, 'shiny_stat': {shiny_stat: None}, '$or': [
        {'item_type': {'$in': ['GearItem', 'IngredientItem']}},
        {'item_type': 'MaterialItem', 'tier': tier}
    ]}
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
        stats['tier'] = docs[0].get('tier')
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
