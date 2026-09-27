---
tags:
- sentence-transformers
- cross-encoder
- reranker
base_model: cross-encoder/ms-marco-MiniLM-L12-v2
pipeline_tag: text-ranking
library_name: sentence-transformers
metrics:
- map
- mrr@10
- ndcg@10
model-index:
- name: CrossEncoder based on cross-encoder/ms-marco-MiniLM-L12-v2
  results:
  - task:
      type: cross-encoder-reranking
      name: Cross Encoder Reranking
    dataset:
      name: train eval
      type: train-eval
    metrics:
    - type: map
      value: 0.9074222386760469
      name: Map
    - type: mrr@10
      value: 0.9530654761904762
      name: Mrr@10
    - type: ndcg@10
      value: 0.9102946511015653
      name: Ndcg@10
---

# CrossEncoder based on cross-encoder/ms-marco-MiniLM-L12-v2

This is a [Cross Encoder](https://www.sbert.net/docs/cross_encoder/usage/usage.html) model finetuned from [cross-encoder/ms-marco-MiniLM-L12-v2](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L12-v2) using the [sentence-transformers](https://www.SBERT.net) library. It computes scores for pairs of texts, which can be used for text reranking and semantic search.

## Model Details

### Model Description
- **Model Type:** Cross Encoder
- **Base model:** [cross-encoder/ms-marco-MiniLM-L12-v2](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L12-v2) <!-- at revision 7b0235231ca2674cb8ca8f022859a6eba2b1c968 -->
- **Maximum Sequence Length:** 512 tokens
- **Number of Output Labels:** 1 label
- **Supported Modality:** Text
<!-- - **Training Dataset:** Unknown -->
<!-- - **Language:** Unknown -->
<!-- - **License:** Unknown -->

### Model Sources

- **Documentation:** [Sentence Transformers Documentation](https://sbert.net)
- **Documentation:** [Cross Encoder Documentation](https://www.sbert.net/docs/cross_encoder/usage/usage.html)
- **Repository:** [Sentence Transformers on GitHub](https://github.com/huggingface/sentence-transformers)
- **Hugging Face:** [Cross Encoders on Hugging Face](https://huggingface.co/models?library=sentence-transformers&other=cross-encoder)

### Full Model Architecture

```
CrossEncoder(
  (0): Transformer({'transformer_task': 'sequence-classification', 'modality_config': {'text': {'method': 'forward', 'method_output_name': 'logits'}}, 'module_output_name': 'scores', 'architecture': 'BertForSequenceClassification'})
)
```

## Usage

### Direct Usage (Sentence Transformers)

First install the Sentence Transformers library:

```bash
pip install -U sentence-transformers
```

Then you can load this model and run inference.
```python
from sentence_transformers import CrossEncoder

# Download from the 🤗 Hub
model = CrossEncoder("cross_encoder_model_id")
# Get scores for pairs of inputs
pairs = [
    ['How many calories in an egg', 'There are on average between 55 and 80 calories in an egg depending on its size.'],
    ['How many calories in an egg', 'Egg whites are very low in calories, have no fat, no cholesterol, and are loaded with protein.'],
    ['How many calories in an egg', 'Most of the calories in an egg come from the yellow yolk in the center.'],
]
scores = model.predict(pairs)
print(scores)
# [1.4126 0.2683 0.5038]

# Or rank different texts based on similarity to a single text
ranks = model.rank(
    'How many calories in an egg',
    [
        'There are on average between 55 and 80 calories in an egg depending on its size.',
        'Egg whites are very low in calories, have no fat, no cholesterol, and are loaded with protein.',
        'Most of the calories in an egg come from the yellow yolk in the center.',
    ]
)
# [{'corpus_id': ..., 'score': ...}, {'corpus_id': ..., 'score': ...}, ...]
```

<!--
### Direct Usage (Transformers)

<details><summary>Click to see the direct usage in Transformers</summary>

</details>
-->

<!--
### Downstream Usage (Sentence Transformers)

You can finetune this model on your own dataset.

<details><summary>Click to expand</summary>

</details>
-->

<!--
### Out-of-Scope Use

*List how the model may foreseeably be misused and address what users ought not to do with the model.*
-->

## Evaluation

### Metrics

#### Cross Encoder Reranking

* Dataset: `train-eval`
* Evaluated with [<code>CERerankingEvaluator</code>](https://sbert.net/docs/package_reference/cross_encoder/evaluation.html#sentence_transformers.cross_encoder.evaluation.CERerankingEvaluator) with these parameters:
  ```json
  {
      "at_k": 10
  }
  ```

| Metric      | Value      |
|:------------|:-----------|
| map         | 0.9074     |
| mrr@10      | 0.9531     |
| **ndcg@10** | **0.9103** |

<!--
## Bias, Risks and Limitations

*What are the known or foreseeable issues stemming from this model? You could also flag here known failure cases or weaknesses of the model.*
-->

<!--
### Recommendations

*What are recommendations with respect to the foreseeable issues? For example, filtering explicit content.*
-->

## Training Details

### Training Logs
| Epoch | Step  | train-eval_ndcg@10 |
|:-----:|:-----:|:------------------:|
| 0     | 5000  | 0.9055             |
| 0     | 10000 | 0.9129             |
| 0     | 15000 | 0.9097             |
| 0     | 20000 | 0.9102             |
| 0     | 25000 | 0.9094             |
| 0     | -1    | 0.9103             |


### Framework Versions
- Python: 3.10.21
- Sentence Transformers: 6.1.0
- Transformers: 5.17.0
- PyTorch: 2.5.1
- Accelerate: 1.15.0
- Datasets: 4.1.1
- Tokenizers: 0.23.2

## Additional Resources

- [Training and Finetuning Reranker Models with Sentence Transformers](https://huggingface.co/blog/train-reranker): the end-to-end guide for training or finetuning Cross Encoder (reranker) models.
- [Multimodal Embedding & Reranker Models with Sentence Transformers](https://huggingface.co/blog/multimodal-sentence-transformers): use text, image, audio, and video reranker models through the same API.
- [Training and Finetuning Multimodal Embedding & Reranker Models with Sentence Transformers](https://huggingface.co/blog/train-multimodal-sentence-transformers): training multimodal Cross Encoders.

## Citation

### BibTeX

<!--
## Glossary

*Clearly define terms in order to be accessible across audiences.*
-->

<!--
## Model Card Authors

*Lists the people who create the model card, providing recognition and accountability for the detailed work that goes into its construction.*
-->

<!--
## Model Card Contact

*Provides a way for people who have updates to the Model Card, suggestions, or questions, to contact the Model Card authors.*
-->