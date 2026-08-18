# github actions to build binaries.

I've just commited this parent folder as a github repository. Can you add a configuration for gitactions that builds the binaries for wxwidgets-test1 for platforms linux, windows and macos ? Is it possible ? 

make sure .github/workflows/wxwidgets-test1-build.yml doesn't run automatically, and only explicitly pressing a button, is that possible ? 



# pages / website
This folder contains several desktop app projects using wxwindows & python. They work and are useful and I plan to add more 
they compile successfully in gitlab actions .github/workflows to executables for linux, macos and windows.
I would like now to create homepages for each of them, goodlooking and with screenshots or recordings of their features since I think they are cool and lightweight (have advantage over similar opensource tools.)
Please analyze and recommend an approach to create websites for each app using github pages maybe, and if you can elaborate all this material yourself or you need me to perform some screenshots or screen recording. 
I would like that documents generates to be able to be updated both by claude or myself a human.
For now these apps won't have a reference manual or guide, just a single html with screenshots and data and some small instructions & tutorials
Don't write any code, first plan how would you perform this so we have a template / framework in place to document any of the existing a new desktop apps in this folder


# better linux distributions
currently, the github action files located at .github/workflows build a linux bundle which is very heavy both in distribution size and exec memory. I wonder if we can build binaries for arch linux to distribute on AUR which use dynamic link and be lightweight. Can you, without writin code, tell me if that0s possible using pyinstaller or other tools ? 

# missing docs
in docs/index.html we've created some html docs user facing for each docs/my-data-viewer docs/my-redis-viewer project's. Now complete the documentation to also add projects (my-disk-viewer) (my-docker-viewer) and for existing projects make sure all their features are mentioned. Notice that this documentation is for each application homepage, add placeholders for relevant screenshots and I will add them myself later




---


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
