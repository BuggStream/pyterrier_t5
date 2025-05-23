import json
import ir_datasets
import pandas as pd
import pyterrier as pt
pt.java.init()
from pyterrier.measures import *
from pyterrier_t5 import MonoT5ReRanker
from transformers import T5ForConditionalGeneration, T5Tokenizer, Adafactor
from random import Random
import itertools

BATCH_SIZE = 16

import torch
torch.manual_seed(0)

_logger = ir_datasets.log.easy()
DEVICE = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
OUTPUTS = ['true', 'false']

def iter_train_samples():
  dataset = ir_datasets.load('msmarco-passage/train/triples-small')
  docs = dataset.docs_store()
  queries = {q.query_id: q.text for q in dataset.queries_iter()}
  while True:
    for qid, dida, didb in dataset.docpairs_iter():
      yield 'Query: ' + queries[qid] + ' Document: ' + docs.get(dida).text + ' Relevant:', OUTPUTS[0]
      yield 'Query: ' + queries[qid] + ' Document: ' + docs.get(didb).text + ' Relevant:', OUTPUTS[1]

train_iter = _logger.pbar(iter_train_samples(), desc='total train samples')

model = T5ForConditionalGeneration.from_pretrained("t5-base").to(DEVICE)
tokenizer = T5Tokenizer.from_pretrained("t5-base")
optimizer = Adafactor(model.parameters(), lr=5e-5, relative_step=False)


reranker = MonoT5ReRanker(verbose=False, batch_size=BATCH_SIZE)
reranker.REL = tokenizer.encode(OUTPUTS[0])[0]
reranker.NREL = tokenizer.encode(OUTPUTS[1])[0]

def build_validation_data():
  result = []
  dataset = ir_datasets.load('msmarco-passage/trec-dl-2019/judged')
  docs = dataset.docs_store()
  queries = {q.query_id: q.text for q in dataset.queries_iter()}
  for qrel in _logger.pbar(ir_datasets.load('msmarco-passage/dev').qrels_iter(), desc='dev data'):
    if qrel.query_id in queries:
      result.append([qrel.query_id, queries[qrel.query_id], qrel.doc_id, docs.get(qrel.doc_id).text])
  return pd.DataFrame(result, columns=['qid', 'query', 'docno', 'text'])

valid_data = build_validation_data()
valid_qrels = pt.get_dataset('irds:msmarco-passage/trec-dl-2019/judged').get_qrels()

epoch = 0

max_ndcg = 0.

with _logger.pbar_raw(desc=f'train {epoch}', total=16384 // BATCH_SIZE) as pbar:
  model.train()
  total_loss = 0
  count = 0
  for _ in range(16384 // BATCH_SIZE):
    inp, out = [], []
    for i in range(BATCH_SIZE):
      i, o = next(train_iter)
      inp.append(i)
      out.append(o)
    inp_ids = tokenizer(inp, return_tensors='pt', padding=True).input_ids.to(DEVICE)
    out_ids = tokenizer(out, return_tensors='pt', padding=True).input_ids.to(DEVICE)
    loss = model(input_ids=inp_ids, labels=out_ids).loss
    loss.backward()
    optimizer.step()
    optimizer.zero_grad()
    total_loss = loss.item()
    count += 1
    pbar.update(1)
    pbar.set_postfix({'loss': total_loss/count})
with _logger.duration(f'valid {epoch}'):
  reranker.model = model
  reranker.verbose = True
  res = reranker(valid_data)
  reranker.verbose = False
  model.save_pretrained(f'./mymodel-best-{epoch}')
