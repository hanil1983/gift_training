import io
import zipfile
from pathlib import Path

import pandas as pd
import requests


SYMBOL = "BTCUSDT"
INTERVAL = "15m"
YEARS = 5

OUTPUT_DIR = Path(__file__).resolve().parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "BTCUSDT_15m_5y.parquet"

COLUMNS = [
    "OpenTime", "Open", "High", "Low", "Close", "Volume",
    "CloseTime", "QuoteVolume", "Trades",
    "TakerBuyBase", "TakerBuyQuote", "Ignore",
]


def parse_open_time(series):
    values = pd.to_numeric(
        series,
        errors="coerce",
    )

    valid = values.dropna()

    if valid.empty:
        return pd.to_datetime(
            values,
            errors="coerce",
        )

    # Binance Vision Spot archive는 2025년 이후
    # microseconds timestamp가 포함될 수 있어 자동 판별합니다.
    typical = float(valid.median())
    unit = "us" if typical >= 1e14 else "ms"

    return pd.to_datetime(
        values,
        unit=unit,
        errors="coerce",
        utc=True,
    ).dt.tz_convert(None)


def normalize(df):
    df["OpenTime"] = parse_open_time(
        df["OpenTime"]
    )

    for col in [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    ]:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    df = df.dropna(
        subset=[
            "OpenTime",
            "Open",
            "High",
            "Low",
            "Close",
            "Volume",
        ]
    )

    df = df.set_index("OpenTime")

    return df[
        ["Open", "High", "Low", "Close", "Volume"]
    ]


def download_month(session, month):
    month_str = month.strftime("%Y-%m")

    url = (
        "https://data.binance.vision/data/spot/monthly/klines/"
        f"{SYMBOL}/{INTERVAL}/"
        f"{SYMBOL}-{INTERVAL}-{month_str}.zip"
    )

    response = session.get(
        url,
        timeout=60,
    )

    if response.status_code == 404:
        print(f"  - {month_str}: archive 없음, 건너뜀")
        return None

    response.raise_for_status()

    with zipfile.ZipFile(
        io.BytesIO(response.content)
    ) as zf:
        csv_files = [
            name
            for name in zf.namelist()
            if name.lower().endswith(".csv")
        ]

        if not csv_files:
            return None

        with zf.open(csv_files[0]) as f:
            df = pd.read_csv(
                f,
                header=None,
                names=COLUMNS,
            )

    return normalize(df)


def download_recent_rest(
    session,
    start_time,
    end_time,
):
    url = (
        "https://data-api.binance.vision/api/v3/klines"
    )

    cursor = int(
        pd.Timestamp(start_time).timestamp()
        * 1000
    )
    end_ms = int(
        pd.Timestamp(end_time).timestamp()
        * 1000
    )

    chunks = []

    while cursor <= end_ms:
        response = session.get(
            url,
            params={
                "symbol": SYMBOL,
                "interval": INTERVAL,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            },
            timeout=30,
        )
        response.raise_for_status()

        rows = response.json()

        if not rows:
            break

        part = pd.DataFrame(
            rows,
            columns=COLUMNS,
        )

        # REST API timestamp는 milliseconds
        part["OpenTime"] = pd.to_datetime(
            pd.to_numeric(
                part["OpenTime"],
                errors="coerce",
            ),
            unit="ms",
            errors="coerce",
            utc=True,
        ).dt.tz_convert(None)

        for col in [
            "Open",
            "High",
            "Low",
            "Close",
            "Volume",
        ]:
            part[col] = pd.to_numeric(
                part[col],
                errors="coerce",
            )

        part = (
            part.dropna(
                subset=[
                    "OpenTime",
                    "Open",
                    "High",
                    "Low",
                    "Close",
                    "Volume",
                ]
            )
            .set_index("OpenTime")
        )

        chunks.append(
            part[
                [
                    "Open",
                    "High",
                    "Low",
                    "Close",
                    "Volume",
                ]
            ]
        )

        last_open_ms = int(rows[-1][0])

        if last_open_ms < cursor:
            break

        cursor = last_open_ms + 1

        if len(rows) < 1000:
            break

    if not chunks:
        return pd.DataFrame(
            columns=[
                "Open",
                "High",
                "Low",
                "Close",
                "Volume",
            ]
        )

    return pd.concat(chunks)


def main():
    now = (
        pd.Timestamp.now(tz="UTC")
        .tz_localize(None)
    )

    start_cutoff = (
        now - pd.DateOffset(years=YEARS)
    )

    start_month = (
        start_cutoff
        .to_period("M")
        .to_timestamp()
    )

    current_month = (
        now
        .to_period("M")
        .to_timestamp()
    )

    last_complete_month = (
        current_month
        - pd.DateOffset(months=1)
    )

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent":
                "Mozilla/5.0 BTC Trading Replay Data Builder"
        }
    )

    chunks = []

    months = pd.date_range(
        start=start_month,
        end=last_complete_month,
        freq="MS",
    )

    total = len(months)

    print(
        f"BTCUSDT 15분봉 최근 {YEARS}년 데이터를 준비합니다."
    )
    print(
        f"월별 archive {total}개를 확인합니다."
    )

    for i, month in enumerate(
        months,
        start=1,
    ):
        month_str = month.strftime("%Y-%m")
        print(
            f"[{i:02d}/{total:02d}] {month_str}",
            end="",
            flush=True,
        )

        try:
            df = download_month(
                session,
                month,
            )

            if (
                df is not None
                and
                not df.empty
            ):
                chunks.append(df)
                print(
                    f"  {len(df):,}봉"
                )
            else:
                print("  건너뜀")

        except Exception as e:
            print(
                f"  실패: {e}"
            )
            raise

    if chunks:
        historical = pd.concat(
            chunks
        ).sort_index()

        rest_start = (
            historical.index.max()
            + pd.Timedelta(minutes=15)
        )

    else:
        historical = pd.DataFrame(
            columns=[
                "Open",
                "High",
                "Low",
                "Close",
                "Volume",
            ]
        )
        rest_start = start_cutoff

    print("최신 구간을 REST API로 보충합니다...")

    recent = download_recent_rest(
        session,
        rest_start,
        now,
    )

    pieces = [
        df
        for df in [
            historical,
            recent,
        ]
        if not df.empty
    ]

    if not pieces:
        raise RuntimeError(
            "BTC 데이터를 가져오지 못했습니다."
        )

    df = pd.concat(pieces)

    df = (
        df[
            ~df.index.duplicated(
                keep="last"
            )
        ]
        .sort_index()
    )

    df = df[
        (df.index >= start_cutoff)
        &
        (df.index <= now)
    ]

    df = df[
        ["Open", "High", "Low", "Close", "Volume"]
    ].dropna()

    df.index.name = "OpenTime"

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    df.to_parquet(
        OUTPUT_FILE,
        engine="pyarrow",
        compression="zstd",
    )

    size_mb = (
        OUTPUT_FILE.stat().st_size
        / 1024
        / 1024
    )

    print()
    print("완료")
    print(
        f"기간: {df.index.min()} ~ {df.index.max()}"
    )
    print(
        f"15분봉: {len(df):,}개"
    )
    print(
        f"파일: {OUTPUT_FILE}"
    )
    print(
        f"크기: {size_mb:.1f} MB"
    )
    print()
    print(
        "이제 data/BTCUSDT_15m_5y.parquet 파일을 "
        "app.py와 함께 GitHub에 push하세요."
    )


if __name__ == "__main__":
    main()
