# -*- coding: utf-8 -*-
"""Copy of Fine-tune Longformer Encoder-Decoder (LED) for Summarization on pubmed

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1ET8X2U6oPL97qEJWr4ZKGoS12yoXNecc

## Finetuning Pre-trained Language Models for Biomedical Lay Summarization

First, let's try to check GPU specifications
"""

# crash colab to get more RAM
!kill -9 -1

"""To check that we are having enough RAM we can run the following command.
If the randomely allocated GPU is too small, the above cells can be run 
to crash the notebook hoping to get a better GPU.
"""

!nvidia-smi

# Commented out IPython magic to ensure Python compatibility.
# %%capture
# ! pip install datasets transformers rouge-score nltk

"""Let's start by loading and preprocessing the dataset.


"""

import transformers
print(transformers.__version__)

from datasets import load_dataset, load_metric

"""Next, we download the PLOS & eLife train and validation dataset"""

train_dataset = load_dataset("tomasg25/scientific_lay_summarisation","elife",split="train")
val_dataset = load_dataset("tomasg25/scientific_lay_summarisation","elife", split="validation")

"""It's always a good idea to take a look at some data samples. Let's do that here."""

import datasets
import random
import pandas as pd
from IPython.display import display, HTML

def show_random_elements(dataset, num_examples=4):
    assert num_examples <= len(dataset), "Can't pick more elements than there are in the dataset."
    picks = []
    for _ in range(num_examples):
        pick = random.randint(0, len(dataset)-1)
        while pick in picks:
            pick = random.randint(0, len(dataset)-1)
        picks.append(pick)
    
    df = pd.DataFrame(dataset[picks])
    for column, typ in dataset.features.items():
        if isinstance(typ, datasets.ClassLabel):
            df[column] = df[column].transform(lambda i: typ.names[i])
    display(HTML(df.to_html()))

"""We can see that the input data is the `article` - a scientific report and the target data is the `summary` - a lay summary of the report."""

from transformers import AutoTokenizer

!pip install sentencepiece

tokenizer = AutoTokenizer.from_pretrained("ainize/bart-base-cnn") # flax-community/t5-base-cnn-dm, ainize/bart-base-cnn, google/mt5-small, allenai/led-base-16384 sshleifer/distill-pegasus-xsum-16-4

max_input_length = 1024 ##8192
max_output_length = 512
batch_size = 2

"""Now, let's write down the input data processing function that will be used to map each data sample to the correct model format.
As explained earlier `article` represents here our input data and `summary` is the target data. 
"""

def process_data_to_model_inputs(batch):
    # tokenize the inputs and labels
    inputs = tokenizer(
        batch["article"],
        padding="max_length",
        truncation=True,
        max_length=max_input_length,
    )
    outputs = tokenizer(
        batch["summary"],
        padding="max_length",
        truncation=True,
        max_length=max_output_length,
    )

    batch["input_ids"] = inputs.input_ids
    batch["attention_mask"] = inputs.attention_mask

    # create 0 global_attention_mask lists
    batch["global_attention_mask"] = len(batch["input_ids"]) * [
        [0 for _ in range(len(batch["input_ids"][0]))]
    ]

    # since above lists are references, the following line changes the 0 index for all samples
    batch["global_attention_mask"][0][0] = 1
    batch["labels"] = outputs.input_ids

    # We have to make sure that the PAD token is ignored
    batch["labels"] = [
        [-100 if token == tokenizer.pad_token_id else token for token in labels]
        for labels in batch["labels"]
    ]

    return batch

"""Great, having defined the mapping function, let's preprocess the training data"""

train_dataset = train_dataset.map(
    process_data_to_model_inputs,
    batched=True,
    batch_size=batch_size,
    remove_columns=["article", "summary"],
)

"""and validation data"""

val_dataset = val_dataset.map(
    process_data_to_model_inputs,
    batched=True,
    batch_size=batch_size,
    remove_columns=["article", "summary"],
)

"""Finally, the datasets should be converted into the PyTorch format as follows."""

train_dataset.set_format(
    type="torch",
    columns=["input_ids", "attention_mask", "global_attention_mask", "labels"],
)
val_dataset.set_format(
    type="torch",
    columns=["input_ids", "attention_mask", "global_attention_mask", "labels"],
)

"""Let's load the model via the `AutoModelForSeq2SeqLM` class."""

from transformers import AutoModelForSeq2SeqLM

led = AutoModelForSeq2SeqLM.from_pretrained("ainize/bart-base-cnn", gradient_checkpointing=True, use_cache=False)  ## ainize/bart-base-cnn sshleifer/distill-pegasus-xsum-16-4,  allenai/led-base-16384

"""During training, we want to evaluate the model on Rouge, the most common metric used in summarization, to make sure the model is indeed improving during training."""

# set generate hyperparameters
led.config.num_beams = 2
led.config.max_length = 512
led.config.min_length = 100
led.config.length_penalty = 2.0
led.config.early_stopping = True
led.config.no_repeat_ngram_size = 3

"""Next, we also have to define the function the will compute the `"rouge"` score during evalution."""

rouge = load_metric("rouge")

def compute_metrics(pred):
    labels_ids = pred.label_ids
    pred_ids = pred.predictions

    pred_str = tokenizer.batch_decode(pred_ids, skip_special_tokens=True)
    labels_ids[labels_ids == -100] = tokenizer.pad_token_id
    label_str = tokenizer.batch_decode(labels_ids, skip_special_tokens=True)

    rouge_output = rouge.compute(
        predictions=pred_str, references=label_str, rouge_types=["rouge2"]
    )["rouge2"].mid

    return {
        "rouge2_precision": round(rouge_output.precision, 4),
        "rouge2_recall": round(rouge_output.recall, 4),
        "rouge2_fmeasure": round(rouge_output.fmeasure, 4),
    }

"""Now, we're ready to start training. Let's import the `Seq2SeqTrainer` and `Seq2SeqTrainingArguments`."""

from transformers import Seq2SeqTrainer, Seq2SeqTrainingArguments

# enable fp16 apex training
training_args = Seq2SeqTrainingArguments(
    predict_with_generate=True,
    evaluation_strategy="steps",
    per_device_train_batch_size=batch_size,
    per_device_eval_batch_size=batch_size,
    fp16=True,
    output_dir="./",
    logging_steps=5,
    eval_steps=10,
    save_steps=10,
    save_total_limit=2,
    gradient_accumulation_steps=4,
    num_train_epochs=10,
)

"""The training arguments, along with the model, tokenizer, datasets and the `compute_metrics` function can then be passed to the `Seq2SeqTrainer`"""

trainer = Seq2SeqTrainer(
    model=led,
    tokenizer=tokenizer,
    args=training_args,
    compute_metrics=compute_metrics,
    train_dataset=train_dataset,
    eval_dataset=val_dataset,
)

trainer.train()

