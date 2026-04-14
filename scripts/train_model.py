#!/usr/bin/env python3
"""One-shot ML model training for TELEGLAS Pro"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.database import Database
from src.ml.model_trainer import ModelTrainer


async def main():
    db = Database()
    await db.connect()

    trainer = ModelTrainer()
    trainer.set_db(db)

    result = await trainer.train()
    if result:
        m = result['metrics']
        print(f"✅ Model trained: {m['model_type']}")
        print(f"   AUC: {m.get('auc', 0):.3f}")
        print(f"   Accuracy: {m.get('accuracy', 0):.3f}")
        print(f"   Samples: {m['n_samples']}")
        print(f"   Saved: {result['model_path']}")
        if 'top_features' in m:
            print("   Top features:")
            for f in m['top_features'][:5]:
                print(f"     - {f['name']}: {f['importance']:.3f}")
    else:
        print("❌ Training failed (not enough data?)")

    await db.close()


asyncio.run(main())
