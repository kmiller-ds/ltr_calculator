# LTR Forecast v2

This is a separate, reorganized version of the retention and lifetime revenue app. It does not modify the original project.

## Run

Install [UV](https://docs.astral.sh/uv/getting-started/installation/) if it is not already available, then sync the project dependencies:

```powershell
uv sync
```

Authenticate Google Cloud access with Application Default Credentials:

```powershell
gcloud auth application-default login
```

From this folder, run:

```powershell
uv run python app.py
```

Open `http://127.0.0.1:5001`. Set the `PORT` environment variable to use a different port.

## Data behavior

The app loads the configured source object from Google Cloud Storage. It accepts the current `Retention Drops` workbook layout and already-tabular CSV or Excel data with `CM Site Name`, `Offer`, `Offer Length`, `Drop 1` through `Drop 4`, and optionally `Intro Price` columns.

Offer and site choices are derived from loaded rows. If an `Intro Price` column exists, the selected site's offer price comes from that column and updates when the site or offer changes. The current workbook layout does not include that normalized field, so the loader retains the existing $1 monthly / $8.25 annual prices as a compatibility fallback. Other calculation assumptions remain editable model defaults because they are not part of the current source rows.

### User-provided source file format

Choose a CSV or Excel file in the **Source file** field to upload and validate it immediately. The app checks the columns and values and shows an error if the file is invalid; validation does not run a forecast. After successful validation, select **Run forecast**. The file is limited to 16 MB and remains selected in the current app process for up to 30 minutes after its last use; it is not preserved across app restarts. Choose another file to replace it, or select **Reset** to return to the configured source.

The uploaded file should use this tabular format, with the column names exactly as shown:

| CM Site Name | Offer | Offer Length | Drop 1 | Drop 2 | Drop 3 | Drop 4 | Intro Price |
|---|---|---:|---:|---:|---:|---:|---:|
| Example Site | Offer One | 4 | 0.10 | 0.08 | 0.06 | 0.04 | 1.00 |
| Example Site | Offer Two | 52 | 0.12 | 0.09 | 0.07 | 0.05 | 8.25 |

Use one row per site and offer. A site can have multiple offers, represented by separate rows with the same `CM Site Name` and a different `Offer`. `Offer Length` is in weeks, drop values are decimal rates (for example, `0.10` means 10%), and `Intro Price` is the offer price. For Excel files, put the table and headers in the first worksheet, starting in the first row.

## Layout

- `app.py`: Flask routes and request handling
- `model.py`: source loading, normalization, and forecast calculations
- `templates/index.html`: page structure
- `static/css/app.css`: responsive USA TODAY-inspired styling
- `static/js/app.js`: source-driven offer and intro-price updates
- `tests/test_model.py`: model and UI regression checks

Run the focused tests from this folder:

```powershell
uv run python -m unittest discover -s tests -v
```

For deployment, set `FLASK_SECRET_KEY` to a secure value and run Flask behind a production WSGI server.