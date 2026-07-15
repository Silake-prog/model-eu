rule run_country_analysis:
    input:
        "data/{country}_{year}_merged.parquet"
    output:
        "results/{country}_{year}_model.json"
    params:
        country="{country}",
        year="{year}",
        start=config["start_date"],
        end=config["end_date"]
    script:
        "../scripts/analyze.py"
