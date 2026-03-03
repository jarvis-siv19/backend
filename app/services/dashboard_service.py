from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase
import datetime


def get_datetime_range(request_data: dict) -> list:
    """Parse date range from request data."""
    start = request_data.get("startDate")
    end = request_data.get("endDate")

    if start:
        try:
            start_dt = datetime.datetime.strptime(start, "%a %b %d %Y")
        except ValueError:
            try:
                start_dt = datetime.datetime.fromisoformat(
                    start.replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                start_dt = datetime.datetime.min
    else:
        start_dt = datetime.datetime.min

    if end:
        try:
            end_dt = datetime.datetime.strptime(end, "%a %b %d %Y")
            end_dt = end_dt.replace(hour=23, minute=59, second=59)
        except ValueError:
            try:
                end_dt = datetime.datetime.fromisoformat(end.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                end_dt = datetime.datetime.max
    else:
        end_dt = datetime.datetime.max

    return [start_dt, end_dt]


async def group_owe_details(db: AsyncIOMotorDatabase, user_id: str) -> tuple:
    """Calculate total owed and owing amounts for group/friend/settle expenses."""
    pipeline = [
        {
            "$match": {
                "type": {"$in": ["group", "friend", "settle"]},
                "shares.userId": ObjectId(user_id),
            }
        },
        {"$project": {"payShares": 1, "shares": 1, "type": 1}},
    ]

    result = await db.expense.aggregate(pipeline).to_list(length=None)

    total_owed_amount = 0  # others owe me
    total_owe_amount = 0  # I owe others

    for expense in result:
        shares = expense.get("shares", [])
        pay_shares = expense.get("payShares", [])
        total_paid = (
            sum(float(p["amount"]) for p in pay_shares) if pay_shares else 0
        )

        for share in shares:
            participant_id = str(share["userId"])
            share_amount = float(share["amount"])

            for ps in pay_shares:
                payer_id = str(ps["userId"])
                pay_amount = float(ps["amount"])

                if total_paid == 0:
                    continue

                proportional_amount = share_amount * (pay_amount / total_paid)

                if payer_id == participant_id:
                    continue

                if participant_id == user_id:
                    total_owe_amount += proportional_amount
                elif payer_id == user_id:
                    total_owed_amount += proportional_amount

    return (round(total_owe_amount, 2), round(total_owed_amount, 2))


async def get_summary_for_homepage(
    db: AsyncIOMotorDatabase, user_id: str, request_data: dict
) -> dict:
    """Get dashboard summary with expense totals and owe amounts."""
    user = await db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        return None

    date_range = get_datetime_range(request_data)
    total_share = 0

    # Aggregate personal expenses where user paid
    personal_pipeline = [
        {
            "$match": {
                "type": "normal",
                "payShares.userId": ObjectId(user_id),
                "date": {"$gte": date_range[0], "$lte": date_range[1]},
            }
        },
        {"$unwind": "$payShares"},
        {"$match": {"payShares.userId": ObjectId(user_id)}},
        {
            "$group": {
                "_id": "$payShares.userId",
                "totalAmount": {"$sum": "$payShares.amount"},
            }
        },
    ]

    result = await db.expense.aggregate(personal_pipeline).to_list(length=None)
    personal_expenses = float(result[0]["totalAmount"]) if result else 0.0

    # Group/friend/settle expenses
    other_pipeline = [
        {
            "$match": {
                "type": {"$in": ["group", "friend", "settle"]},
                "date": {"$gte": date_range[0], "$lte": date_range[1]},
                "shares.userId": ObjectId(user_id),
            }
        },
        {"$project": {"payShares": 1, "type": 1, "shares": 1}},
    ]

    result = await db.expense.aggregate(other_pipeline).to_list(length=None)
    for expense in result:
        for share in expense.get("shares", []):
            if str(share["userId"]) == user_id and expense.get("type") != "settle":
                total_share += float(share["amount"])

    total_owe_amount, total_owed_amount = await group_owe_details(db, user_id)

    return {
        "group_expenses": float(total_share),
        "personal_expenses": personal_expenses,
        "total_you_owe": float(total_owe_amount),
        "total_owed_to_you": float(total_owed_amount),
        "username": user.get("name", ""),
    }


async def get_dashboard_chart(
    db: AsyncIOMotorDatabase, user_id: str, request_data: dict
) -> list:
    """Get category-wise expense breakdown for chart display."""
    date_range = get_datetime_range(request_data)

    pipeline = [
        {
            "$match": {
                "type": "normal",
                "date": {"$gte": date_range[0], "$lte": date_range[1]},
            }
        },
        {"$unwind": "$payShares"},
        {"$match": {"payShares.userId": ObjectId(user_id)}},
        {
            "$group": {
                "_id": None,
                "totalCost": {"$sum": "$payShares.amount"},
                "categoryData": {
                    "$push": {
                        "category": "$category",
                        "cost": "$payShares.amount",
                    }
                },
            }
        },
        {"$unwind": "$categoryData"},
        {
            "$group": {
                "_id": "$categoryData.category",
                "cost": {"$sum": "$categoryData.cost"},
                "noOfTransactions": {"$sum": 1},
                "totalCost": {"$first": "$totalCost"},
            }
        },
        {
            "$project": {
                "category": "$_id",
                "cost": 1,
                "noOfTransactions": 1,
                "percent": {
                    "$multiply": [{"$divide": ["$cost", "$totalCost"]}, 100]
                },
            }
        },
        {"$sort": {"cost": -1}},
    ]

    result = await db.expense.aggregate(pipeline).to_list(length=None)
    return [
        {k: v for k, v in entry.items() if k != "_id"} for entry in result
    ]
