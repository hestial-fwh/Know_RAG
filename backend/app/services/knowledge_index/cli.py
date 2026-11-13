"""运行：python -m app.services.knowledge_index.cli --once --reconcile。"""

import argparse
import asyncio
import json

from app.db.session import SessionLocal
from app.services.knowledge_index.service import reconcile
from app.services.knowledge_index.worker import run_once
from app.services.knowledge_index.communities import run_graph_once


async def main():
    parser = argparse.ArgumentParser(description="本地派生索引持久任务消费者")
    parser.add_argument("--once", action="store_true", help="最多消费一个任务后退出")
    parser.add_argument("--reconcile", action="store_true", help="消费前补偿扫描已开启索引的知识库")
    args = parser.parse_args()
    while True:
        if args.reconcile:
            with SessionLocal() as db:
                reconcile(db)
                db.commit()
        consumed = await run_once(SessionLocal)
        if not consumed:
            consumed = await run_graph_once(SessionLocal)
        if args.once:
            print(json.dumps({"consumed": consumed}))
            return
        if not consumed:
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
