"""Metrics explicitly distinguish annotated negatives from unannotated labels."""
import math

def wilson(successes,total,z=1.959963984540054):
    if not total: return None
    p=successes/total;d=1+z*z/total;c=(p+z*z/(2*total))/d
    h=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/d
    return [max(0.,c-h),min(1.,c+h)]

def counts_to_metrics(tp,fp,fn,tn=0):
    return dict(precision_wilson95=wilson(tp,tp+fp),recall_wilson95=wilson(tp,tp+fn),tp=tp,fp=fp,fn=fn,tn=tn,precision=tp/(tp+fp) if tp+fp else None,recall=tp/(tp+fn) if tp+fn else None,f1=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None)

def presence_metrics(records,predictions,labels,closed_set=False):
    out={}
    for label in labels:
        tp=fp=fn=tn=unknown=0
        for r in records:
            pos=label in r.get('labels',[]);neg=label in r.get('negative_labels',[]) or closed_set
            pred=label in predictions.get(r['id'],set())
            if not pos and not neg: unknown+=1;continue
            tp+=int(pos and pred);fn+=int(pos and not pred);fp+=int(not pos and pred);tn+=int(not pos and not pred)
        out[label]=dict(counts_to_metrics(tp,fp,fn,tn),unknown=unknown,positive_count=tp+fn,known_negative_count=fp+tn)
        if not closed_set and fp+tn==0:
            # A positive-only annotation set cannot establish precision or F1.
            out[label].update(precision=None,f1=None,precision_wilson95=None,precision_identifiable=False)
        else: out[label]['precision_identifiable']=True
    return out

def interval_metrics(gold,pred,tolerance=2.5):
    """One-to-one maximum matching by endpoint collar; broad support is not onset gold."""
    edges={i:[j for j,p in enumerate(pred) if p['label']==g['label'] and abs(p['start']-g['start'])<=tolerance and abs(p['end']-g['end'])<=tolerance] for i,g in enumerate(gold)}
    matched={}
    def visit(i,seen):
        for j in edges[i]:
            if j in seen:continue
            seen.add(j)
            if j not in matched or visit(matched[j],seen):matched[j]=i;return True
        return False
    for i in edges:visit(i,set())
    return counts_to_metrics(len(matched),len(pred)-len(matched),len(gold)-len(matched))


def group_presence(records,predictions,labels,closed_set=False):
    """Pool clips from one recording before counting independent source support."""
    groups={};pred={}
    for r in records:
        key=r.get('source_group',r['id'])
        if key not in groups: groups[key]=dict(id=key,labels=set(),negative_labels=set(r.get('negative_labels',[])))
        groups[key]['labels'].update(r.get('labels',[]))
        groups[key]['negative_labels'].intersection_update(r.get('negative_labels',[]))
        pred.setdefault(key,set()).update(predictions.get(r['id'],set()))
    return presence_metrics(list(groups.values()),pred,labels,closed_set)
