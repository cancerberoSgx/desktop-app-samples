

# my-documents view

I plan to implement an app that allow me to index text files so I can search for them - or call an LLM with most relevant documents
In these kind of desktop apps, can you recommend me an embedding - semantic search that can be used to index text documents for later search in a normal desktop (without gpu) to perfomm semantic/similarity searches ? Even if the user has to provide an openai / gemini apikey

A
Good news: for text-file indexing at desktop scale, you don't need a GPU or even an API key — small embedding models run fine on CPU. Here's what I'd recommend for a wxPython desktop app:

## My recommendation: local embeddings + SQLite

**Embeddings: `fastembed`** (from the Qdrant team). It runs models via ONNX Runtime, so there's no PyTorch dependency — that matters a lot for a desktop app, because `sentence-transformers` drags in ~2 GB of torch, while fastembed is a few tens of MB plus the model. The default model (`BAAI/bge-small-en-v1.5`, ~130 MB) embeds a few hundred short chunks per second on a normal CPU, which is plenty for indexing personal document collections.

```python
from fastembed import TextEmbedding
model = TextEmbedding("BAAI/bge-small-en-v1.5")
vectors = list(model.embed(chunks))  # 384-dim numpy arrays
```

If indexing speed ever becomes a problem (huge corpora, old hardware), look at **`model2vec`** (e.g. `minishlab/potion-base-8M`): static embeddings, ~30 MB, roughly 100× faster than transformer models on CPU, at a modest quality cost. It's a good "fast mode" option.

**Storage: `sqlite-vec`** — a SQLite extension for vector search. Perfect fit for desktop: single-file database, no server process, and you get metadata (path, mtime, chunk offsets) in the same file as the vectors. Brute-force cosine search over even ~1M small vectors is subsecond, so you don't need a real ANN index at this scale.

**Bonus that pairs beautifully with SQLite: hybrid search.** SQLite's built-in FTS5 gives you BM25 keyword search for free. Combine FTS5 + vector scores (e.g. reciprocal rank fusion) and you get noticeably better retrieval than either alone — keyword search catches exact identifiers/filenames that embeddings fuzz over. This is basically what commercial RAG stacks do.

## Where API keys fit

Local embeddings are good enough for retrieval, so I'd make API keys optional and use them for what they're actually better at:

- **OpenAI `text-embedding-3-small`** (~$0.02 per million tokens) or **Gemini's embedding model** as an optional "higher quality / multilingual" embedding backend. Caveat: switching embedding models means re-embedding the whole corpus, so store which model produced each index.
- The **LLM call over retrieved documents** (your "ask the LLM with most relevant docs" feature) is where the key is genuinely needed — retrieve top-k chunks locally, stuff them into the prompt, send to OpenAI/Gemini/Claude.

## Things that matter more than model choice

- **Chunking**: embed chunks (~300–800 tokens with some overlap), not whole files. Store chunk→file mapping so search results point to a file plus location.
- **Incremental indexing**: hash/mtime check per file so re-index only touches changed files.
- **First-run model download**: fastembed downloads the model on first use (~130 MB) — show progress in the UI and cache it in your app's data dir.

So the stack is: `fastembed` + `sqlite-vec` + FTS5 hybrid, with an optional OpenAI/Gemini backend for embeddings and for the answer-generation step. Alternatives like ChromaDB or LanceDB in embedded mode also work, but they're heavier dependencies for what, in a desktop app, SQLite already does well.


p2
implement the suggested "my-documents-view" section in file prompts.md: So the stack is: `fastembed` + `sqlite-vec` + FTS5 hybrid, with an optional OpenAI/Gemini backend for embeddings and for the answer-generation step. 

create a new project my-documents-viewer in folder "./my-documents-viewer" . Use ./my-redis-viewer project as a skeleton and copy all its features as sqlite db and migrations, preferences, .github/workflows/my-redis-viewer-build.yml . Model profiles so user kind index different kind of documents on each profile, for example, "history", "development", "contracts", etc.
This new project allows users to index documents both for fulltext and vector similarity using embeddings for easy search local docs. 
For embeddings user can select different models, by default fastembed, but also other embeddings like openai and gemini (user provides apikeys in their profile)
uses sqlite-vec to store embeddings, vector dimentions are defined depending on the model choose in the profile.
For now users can only input text files (txt, md) but in the future we'll implement text extractors.
Also implement a ./my-documents-viewer/probes with a simple python script that indexes some texts with fastembed and perform a search in sqlite-vec to easily test the quality.




---

in probes/index_and_search.py add also another experiment but in spanish with accents - leave the english experiment as it's now (don't delete it)
---



# chunk size
currently it seems the document text is splited into chunks of aprox  800 chars which seems very small. Question, this is because the embedding model used has little dimensions ? what's the logic for chunking ? can we increase the chunk size ? (don't write code, just answer)

p2
ok, move the chunk size value to the profiles table. In the edit/create profile let the user pick the chunk size optionally. By default keep it as it is. When profile is save, check first if it fits correctly on the model's max input token length,



# search documents
currently when I search something, several snippets of the same document are displayed. Our users need to see only one document and optionally the relevant chunks.
Question, assuming the documents will be 100% text files, is it possible to display the entire content in an internal text viewer and make a virtual table of content that points and hightlight matched relevant chunks ? (don't write code, just plan and explain how would you implement this in a user friendly manner)

p2
in new profile and edit profile dialog I cannot see the "save" buttons, enlarge it 

p3
when clicking a document the document's content and chunks are displayed in a separate window. Also chunks myst be sort by score and score displayed.

p3
in the documents view chunk highlight color is OK, but remove the highlight color of the non-chunk text, shouldn't be highlighted at all (currently bluish)

p4
in the documents list, if I double click a document the document opens using the same text view as in search result double click (without chunks)

p5
remove exit and about options from sidebar
In menu help->about, complete with more information displaying the home page https://github.com/cancerberoSgx/desktop-app-samples and info about indexing and semantic search, fulltext, hybrid, chat with your docs, and other features

p6
when a document is opened from the "documents" list, only the document's content should be displayed, not the chunks part since they are always empty for simple document content view (only make sense for search results)


# file name setting
there's a new setting "file name display" which could have these values: full path, file name, file and parent folder (parentFolder/fileName)
implement a menu File->Settings.. which display the settings dialog which contains the new setting "file name display"  which is a select box with mentioned values
Everywhere a file name is displayed (in documents or search views) this will be respected but on hover the full path is displayed.
The default value for this setting is "file name"



---


# child documents and import data files
Currently user is able to index "text documents" which text is indexed as one big document
Now, user is able to import "data files" such as csv, json (object arrays), jsond, etc which will define multiple "child documents" associated with a "parent document"
When this import happens, the system creates a parent document "foo.csv" without contents, and then many children documents (one per each csv row or json object)
Normal documents like current .md or .txt files are created as "parent documents"
In the documents view, by default it display only parent documents but user can expand a parent document to see its child documents in a tree-like view.
The use case is to create multiple documents from a csv containing for example "products" and user to be able to perform a fulltext search by sku
What do you think about this feature ? makes sense ? should be another product ? something missing like for example, how text is extracted or if we should also store properties metadata somewhere ? 
(just analyze and answer, don't write code)

p2
explain me more about:
Today one document = one file, so indexing N files means N sets of chunk embeddings, bounded by however many files a user reasonably has. A CSV import turns "one file" into potentially tens of thousands of child documents, each triggering its own embed call in index_paths. For local fastembed that's just slower; for openai/gemini backends it's real cost and rate-limit exposure the app has never had to think about. I'd make vectorizing row-imports opt-in (FTS-only by default for data-file imports, embeddings on request) rather than assuming every child document gets the same treatment as a hand-picked .md file.
My objective is users to be able to find one or more child documents both using FTS or semantic - if you don't create a separate embedding for each row, how woudl you accomplish this ? 

p3
import data file /home/sg/Descargas/chat_metrics.csv says Importing... in the UI and nothing happens. Add more print() statements across all steps of importing a data file (csv parsing, item by item, embeddings generation, etc)

p4
on a new profile, when loading my first "data file" i get error "UNIQUE constraint failed: documents.profile_id, documents.parent_document_id, documents.row_key"

p5
in search results table, only parent documents are displayed and user can expand a parent document to see its child documents that matched the search, in a tree-like view, each with their score as currently is implemented for the "documents" view

p7
 * in documents, when double clicking a parent document it says "This is a container document with 41 record(s) - expand it in the Documents tree to browse them." instead list there the children documents in a data-view

 * the text editor allows to search for a string with a search icon button or ctrl-f. The search can be next or prev occurrence and it must respect the current vertical scroll . This apply both when double clicking a document in documents and search results views.

p8
In document view (double click document in documents or search tab) I also see document full path, indexed date, and other relevant document details. For child documents, display parent metadata and child key


# open ai 
for openai or gemini models, when indexing, batch embeddings calls in 100 strings in a single call. If necesary install openai and gemini python sdks



# search parent doc result
in search results, double clicking a parent document opens a document content viewer with the most relevant child documents at the left and clicking them I can see their contents. It display the first child document content by default


# tests
I want to write some integration tests but they should run without displaying an UI, only at the repository and service layers. 
write pytest test which create a new profile (using default local embedding), add two data files a.csv with data {title: foo, description: 'lorem ipsum'} and b.csv with data {title: bar, description: 'lorem ipsum'}. Perform a fulltext search "lorem ipsum" and both should be returned. Perform a fulltext search with "bar" and only b.csv should be returned. 
After the test finishes it should delete all created profiles and documents.


# The "generate embeddings now" confirmation modal, can be turned off in the modal "don't advice me again" and also in settings.


---

# FUTURE

# search embeddings should be cached (diskcache?)

# chat
I want users to be able to "chat" with their documents. Asking questions like "which years Argentina won the world cup?" or "give me the skus of red shirts". 
Also I want to support conversations, for example multiple questions to refine a search. example: q1: "how many times Argentina won the world cup", q2: "on which years?", q3: "who played the final on 1986?" -all these questions in the same context.
Also users should be able to create new conversations, go back to previous conversations, delete conversations, update/rename existing conversations.
Also I want to support product references, when the chat respond if there's a referenced document user should be able to click it and see the entire document details and content with the matched chunk or child document highlighted
Chat should be able to use openai models or gemini models.
Create a plan to implement so I can review &confirm before writing any code


# doc tags

when a document is added or by editing a document, user can create or add tags to the document, User has a view to see all their tags and associated documents for each in a separate view. Each profile has its tags. In search, user can filtering by tags and verbs : contains all , contains any, don't contain, etc


# issue:
closing the window don't release the current indexing process.


# fts5 fulltext
add https://www.sqlite.org/fts5.html so we can have powerful fulltext searches too
