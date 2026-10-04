---
inclusion: always
---

# Writing Style

Use ASD-STE100 Simplified Technical English (STE) for all prose in this
repository. This rule applies to humans and to agents.

## Scope

Use STE for these items:

- Commit messages, PR titles, and PR descriptions
- Issue text
- `README.md`, `AGENTS.md`, `log.md`, and all files in `docs/`
- ADRs in `docs/adr/`
- Specs in `.kiro/specs/<feature>/`
- Steering files and skills in `.kiro/`
- Text in code comments and docstrings

Do not change these items:

- Code, identifiers, and config keys
- Commands, file paths, and URLs
- Technical names, product names, and terms in the `## Language`
  glossary in `product.md`
- Quoted error messages and tool output

Keep the current document formats. This includes the ADR layout, the
spec layout, and the Conventional Commits prefix. Apply STE to the
prose only.

## Rules

### Sentences

- Write procedural sentences with a maximum of 20 words.
- Write descriptive sentences with a maximum of 25 words.
- Write one instruction in each sentence.
- Use the imperative for instructions. Example: "Run `make test`."
- Use the active voice. Write "The ETL writes the snapshot," not "The
  snapshot is written by the ETL."
- Keep the articles "a," "an," and "the."

### Paragraphs

- Write one topic in each paragraph.
- Write a maximum of six sentences in each paragraph.

### Words

- Use simple words that have only one meaning.
- Use the same term for the same thing every time.
- Do not use idioms, slang, or phrasal verbs.
- Do not use "-ing" words as nouns or modifiers when a simpler
  structure is possible.

| Do not write | Write |
|---|---|
| utilize | use |
| initiate, commence | start |
| terminate | stop |
| ensure | make sure |
| in order to | to |
| prior to | before |
| set up (verb) | configure, make |
| carry out | do |
| figure out | find |

### Warnings and cautions

Write the command first. Then write the reason.

Example: "Do not run `sam deploy` against prod. The prod deploy is
available only through `deploy.yml`."

## Other rules

STE does not replace the other rules in `AGENTS.md` and in the other
steering files. Those rules continue to apply.
