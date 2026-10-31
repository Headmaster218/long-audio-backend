"""Annotation-only selection. No model outputs/performance input.
Earliest feasible complete boundary, then earliest complete endpoint >=30s.
All segment and lexical-word intervals constrain cuts. No speaker-count filter.
"""
from decimal import Decimal

def select(intervals,minimum_start,min_duration=Decimal('30'),max_duration=Decimal('45.728')):
 rows=[(Decimal(str(a)),Decimal(str(b))) for a,b in intervals if b>a]
 minimum_start=Decimal(str(minimum_start))
 points=sorted({minimum_start}|{v for row in rows for v in row if v>=minimum_start})
 clear=lambda t:not any(a<t<b for a,b in rows)
 for a in points:
  if not clear(a):continue
  for b in points:
   if b-a<min_duration:continue
   if b-a>max_duration:break
   if clear(b) and any(a<=x<y<=b for x,y in rows):return a,b
 raise ValueError('No complete-boundary window fits frozen rule')
