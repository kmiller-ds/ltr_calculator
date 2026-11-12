import unittest
from io import BytesIO

import pandas as pd

from app import create_app
from model import (
    MODEL_DEFAULTS,
    load_uploaded_source,
    normalize_source_data,
    offer_price_map,
    run_model,
)


def prepared_source(intro_price=3.5):
    return pd.DataFrame([{
        "CM Site Name": "Example Site",
        "Offer": "Test Offer",
        "Offer Length": 5,
        "Drop 1": 0.1,
        "Drop 2": 0.08,
        "Drop 3": 0.06,
        "Drop 4": 0.04,
        "Intro Price": intro_price,
    }])


class SourceDataTests(unittest.TestCase):
    def test_explicit_source_price_is_preserved(self):
        source = normalize_source_data(prepared_source(4.75))
        self.assertEqual(source.iloc[0]["Intro Price"], 4.75)
        self.assertEqual(offer_price_map(source)["Example Site"]["Test Offer"], 4.75)

    def test_legacy_sheet_gets_compatibility_prices(self):
        raw = pd.DataFrame([[
            "Example Site", -0.1, -0.08, -0.06, -0.04,
            "annual block", -0.12, -0.09, -0.07, -0.05,
        ]], columns=[f"column_{index}" for index in range(10)])
        raw.loc[len(raw)] = [None] * 10

        source = normalize_source_data(raw)
        monthly = source[source["Offer"] == "Monthly Retention"].iloc[0]
        annual = source[source["Offer"] == "Annual Retention"].iloc[0]
        self.assertEqual(monthly["Intro Price"], 1.0)
        self.assertEqual(annual["Intro Price"], 8.25)
        self.assertEqual(monthly["Drop 1"], 0.1)
        self.assertEqual(annual["Drop 1"], 0.12)
        self.assertEqual(len(source), 2)

    def test_uploaded_csv_uses_tabular_headers(self):
        csv_data = (
            "CM Site Name,Offer,Offer Length,Drop 1,Drop 2,Drop 3,Drop 4,Intro Price\n"
            "Uploaded Site,Offer One,4,0.1,0.08,0.06,0.04,3.25\n"
        ).encode()

        source = load_uploaded_source(csv_data, "source.csv")

        self.assertEqual(source.iloc[0]["CM Site Name"], "Uploaded Site")
        self.assertEqual(source.iloc[0]["Intro Price"], 3.25)

    def test_uploaded_excel_uses_first_row_as_headers(self):
        excel = BytesIO()
        prepared_source().to_excel(excel, index=False)
        source = load_uploaded_source(excel.getvalue(), "source.xlsx")

        self.assertEqual(source.iloc[0]["CM Site Name"], "Example Site")
        self.assertEqual(source.iloc[0]["Offer"], "Test Offer")


class ForecastTests(unittest.TestCase):
    def test_model_returns_five_years_and_uses_source_price(self):
        source = prepared_source(4.75)
        result = run_model({
            **MODEL_DEFAULTS,
            "site": "Example Site",
            "offer": "Test Offer",
            "intro_price": None,
        }, source)

        self.assertEqual(len(result), 260)
        self.assertAlmostEqual(result.iloc[0]["Weekly Rate"], 4.75 * (7 / (365.25 / 12)))
        self.assertEqual(result.iloc[-1]["Year"], 5)

    def test_ui_renders_source_defaults_and_separate_assets(self):
        app = create_app(prepared_source(4.75))
        app.testing = True
        client = app.test_client()
        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Example Site", response.data)
        self.assertIn(b"Test Offer", response.data)
        self.assertIn(b'value="4.75"', response.data)
        self.assertIn(b"<h2>Projection</h2>", response.data)
        self.assertIn(b"Run forecast to see cumulative net revenue.", response.data)
        self.assertIn(b"Run forecast to see retention.", response.data)
        self.assertIn(b"Run forecast to see units.", response.data)
        self.assertIn(b"Run forecast to see the weekly results.", response.data)
        self.assertNotIn(b'href="/download"', response.data)

        uploaded_csv = (
            "CM Site Name,Offer,Offer Length,Drop 1,Drop 2,Drop 3,Drop 4,Intro Price\n"
            "Uploaded Site,Offer One,4,0.1,0.08,0.06,0.04,6.25\n"
        ).encode()
        upload_response = client.post("/", data={
            "source_file": (BytesIO(uploaded_csv), "source.csv"),
            "site": "Example Site",
            "offer": "Test Offer",
            "action": "upload_source",
        }, content_type="multipart/form-data")
        self.assertEqual(upload_response.status_code, 200)
        self.assertIn(b"source.csv uploaded and validated.", upload_response.data)
        self.assertIn(b"Using source.csv", upload_response.data)
        self.assertIn(b"Run forecast to see retention.", upload_response.data)
        self.assertNotIn(b'href="/download"', upload_response.data)

        continued_response = client.post("/", data={
            "site": "Uploaded Site",
            "offer": "Offer One",
            "action": "run_forecast",
        })
        self.assertEqual(continued_response.status_code, 200)
        self.assertIn(b"Projection <span>Uploaded Site / Offer One</span>", continued_response.data)

        download_response = client.get("/download")
        self.assertEqual(download_response.status_code, 200)
        downloaded = pd.read_csv(BytesIO(download_response.data))
        self.assertAlmostEqual(downloaded.iloc[0]["Weekly Rate"], 6.25 * (7 / (365.25 / 12)))

        client.get("/?reset=1")
        forecast_response = client.post("/", data={
            "site": "Example Site",
            "offer": "Test Offer",
            "action": "run_forecast",
        })
        self.assertEqual(forecast_response.status_code, 200)
        self.assertIn(b"Ending retention", forecast_response.data)
        self.assertIn(b"Projection <span>Example Site / Test Offer</span>", forecast_response.data)
        self.assertIn(b"Weekly results", forecast_response.data)
        self.assertIn(b'href="/download"', forecast_response.data)
        self.assertNotIn(b"Run forecast to see", forecast_response.data)
        self.assertNotIn(b"Using source.csv", forecast_response.data)

        for asset in ("/static/css/app.css", "/static/js/app.js"):
            asset_response = client.get(asset)
            self.assertEqual(asset_response.status_code, 200)
            if asset.endswith("app.css"):
                self.assertIn(
                    b".chart-grid { display: grid; grid-template-columns: minmax(0, 1fr);",
                    asset_response.data,
                )
            else:
                self.assertIn(b'sourceFile?.addEventListener("change"', asset_response.data)
                self.assertIn(b'action.value = "upload_source"', asset_response.data)
                self.assertIn(b"sourceForm.requestSubmit()", asset_response.data)
            asset_response.close()

    def test_invalid_upload_is_reported_without_running_forecast(self):
        app = create_app(prepared_source())
        app.testing = True
        client = app.test_client()
        invalid_csv = b"site,offer\nExample Site,Offer One\n"

        response = client.post("/", data={
            "source_file": (BytesIO(invalid_csv), "invalid.csv"),
            "action": "upload_source",
        }, content_type="multipart/form-data")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Could not validate uploaded source", response.data)
        self.assertIn(b"Run forecast to see retention.", response.data)
        self.assertNotIn(b'href="/download"', response.data)

    def test_forecast_requires_file_to_be_uploaded_first(self):
        app = create_app(prepared_source())
        app.testing = True
        client = app.test_client()
        csv_data = (
            "CM Site Name,Offer,Offer Length,Drop 1,Drop 2,Drop 3,Drop 4,Intro Price\n"
            "Uploaded Site,Offer One,4,0.1,0.08,0.06,0.04,3.25\n"
        ).encode()

        response = client.post("/", data={
            "source_file": (BytesIO(csv_data), "source.csv"),
            "action": "run_forecast",
        }, content_type="multipart/form-data")

        self.assertIn(b"Upload and validate the selected source file", response.data)
        self.assertNotIn(b'href="/download"', response.data)


if __name__ == "__main__":
    unittest.main()