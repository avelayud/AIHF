#!/usr/bin/env python3
"""
Adds normalized accounting columns to closed-lot CSVs.
"""

from pathlib import Path
import pandas as pd
from src.parser import load_persona

OUT=Path('output/closed_lots_enriched.csv')

def main(persona='Arjuna'):
    raw=load_persona(persona)
    lots=raw[raw.get('dataset_type').eq('closed_lots')].copy()
    lots['total_gain_loss']=lots['st_gl'].fillna(0)+lots['lt_gl'].fillna(0)
    lots['return_pct_on_proceeds']=lots.apply(lambda r: (r['total_gain_loss']/r['proceeds']*100) if pd.notna(r.get('proceeds')) and r.get('proceeds') not in (0,None) else None, axis=1)
    lots['return_pct_on_cost']=lots.apply(lambda r: (r['total_gain_loss']/r['cost']*100) if pd.notna(r.get('cost')) and r.get('cost') not in (0,None) else None, axis=1)
    OUT.parent.mkdir(exist_ok=True)
    lots.to_csv(OUT,index=False)
    print('Written',OUT)

if __name__=='__main__':
    main()
