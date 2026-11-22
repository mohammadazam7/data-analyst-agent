# Support-bi suite

12 questions over the marts of
[cloud-support-bi](https://github.com/mohammadazam7/cloud-support-bi). The data is
synthetic: one year of cloud support cases generated from a fixed seed, so the warehouse
is the same on every machine.

Build the warehouse there, then point the benchmark at it:

```bash
make install
make models        # generates the data and runs dbt build into data/warehouse.duckdb
analyst bench benchmarks/support-bi/questions.toml \
  --warehouse duckdb:///path/to/cloud-support-bi/data/warehouse.duckdb --gold
```

What makes it harder than the commerce suite: customer tiers are a type 2 dimension, so
"how many customers" means current rows only (s02) and tier questions have to choose
between the tier today and the tier when a case was opened (s04, s06); and the daily
backlog has to be summed per day before it is averaged (s12).
