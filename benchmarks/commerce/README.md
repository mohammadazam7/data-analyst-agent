# Commerce suite

12 questions over the order and customer marts of
[ecommerce-data-ai-platform](https://github.com/mohammadazam7/ecommerce-data-ai-platform),
built from the Olist public e-commerce data set at the commit that project pins (its
`docs/DATA.md` has the source and licence).

Build the warehouse there, then point the benchmark at the DuckDB file:

```bash
commerce download --out data/landing
commerce bronze --landing data/landing --lake data/lake --database data/commerce.duckdb
cd dbt && COMMERCE_DUCKDB=../data/commerce.duckdb dbt build --profiles-dir .
```

Each gold query was run against that build; the expected answers are noted next to the
queries in `questions.toml`. Two questions test whether the agent knows that Olist's
`customer_id` is per order and the person is `customer_unique_id` (c02, c03); the rest
cover counts, filters, grouping, dates and top-k.
