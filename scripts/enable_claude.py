"""One-time: subscribe this AWS account to Claude models on Bedrock Marketplace.

Agreements are per model. The Anthropic use-case form is once per account.

    uv run scripts/enable_claude.py \
      --company "Acme" \
      --website "https://example.com" \
      --use-case "Vehicle damage photo estimating prototype" \
      --model anthropic.claude-sonnet-5 \
      --model anthropic.claude-opus-5-5

Needs IAM: bedrock model-agreement APIs plus aws-marketplace Subscribe/ViewSubscriptions/Unsubscribe.
Region defaults to us-west-2. Use foundation model ids (not us. inference profiles).
Always prints full availability JSON and Converse-smokes us.<model> unless --no-smoke.
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3
from botocore.exceptions import ClientError

DEFAULT_MODELS = (
    "anthropic.claude-sonnet-5",
    "anthropic.claude-opus-5-5",
    "anthropic.claude-sonnet-4-6",
)


def put_use_case(bedrock, company: str, website: str, use_case: str, industry: str) -> None:
    form = {
        "companyName": company[:128],
        "companyWebsite": website[:128],
        "intendedUsers": "0",  # 0 internal, 1 external, 2 both
        "industryOption": industry[:128],
        "otherIndustryOption": "",
        "useCases": use_case[:8192],
    }
    try:
        bedrock.put_use_case_for_model_access(formData=json.dumps(form))
        print("Submitted Anthropic first-time use-case form.")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in {"ConflictException", "ValidationException"}:
            raise
        print(f"Use-case form skipped ({code}).")


def print_availability(availability: dict) -> None:
    """Agreement AVAILABLE != invoke-ready; print the full control-plane view."""
    print(json.dumps(availability, indent=2, default=str))


def smoke_converse(region: str, foundation_model_id: str) -> bool:
    """Converse via the US inference profile; fails closed on AccessDenied."""
    profile = f"us.{foundation_model_id}"
    runtime = boto3.client("bedrock-runtime", region_name=region)
    print(f"smoke converse modelId={profile}")
    try:
        resp = runtime.converse(
            modelId=profile,
            messages=[{"role": "user", "content": [{"text": "Reply with exactly: ok"}]}],
            inferenceConfig={"maxTokens": 8, "temperature": 0},
        )
        text = resp["output"]["message"]["content"][0].get("text")
        print(f"smoke converse OK: {text!r}")
        return True
    except ClientError as exc:
        err = exc.response.get("Error", {})
        print(
            f"smoke converse FAILED {err.get('Code')}: {err.get('Message')}",
            file=sys.stderr,
        )
        return False


def enable_model(bedrock, model_id: str, region: str, *, smoke: bool) -> bool:
    print(f"\n=== {model_id} ===")
    try:
        availability = bedrock.get_foundation_model_availability(modelId=model_id)
    except ClientError as exc:
        print(f"get_foundation_model_availability failed: {exc}", file=sys.stderr)
        return False

    print_availability(availability)
    status = availability.get("agreementAvailability", {}).get("status")
    if status == "AVAILABLE":
        print("Marketplace agreement already AVAILABLE.")
    else:
        try:
            offers = bedrock.list_foundation_model_agreement_offers(
                modelId=model_id, offerType="ALL"
            )
        except ClientError as exc:
            print(f"list_foundation_model_agreement_offers failed: {exc}", file=sys.stderr)
            return False

        offer_list = offers.get("offers") or []
        if not offer_list:
            # Some models are open / not sold via Marketplace offers.
            print("No Marketplace offers. If AWS lists this model as open, invoke it once instead.")
            return False

        token = offer_list[0]["offerToken"]
        print(f"Using offerId={offer_list[0].get('offerId')}")

        try:
            bedrock.create_foundation_model_agreement(modelId=model_id, offerToken=token)
            print("Created foundation model agreement.")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"ConflictException", "AccessAlreadyExistsException"}:
                print(f"Agreement already exists ({code}).")
            else:
                print(f"create_foundation_model_agreement failed: {exc}", file=sys.stderr)
                return False

        availability = bedrock.get_foundation_model_availability(modelId=model_id)
        print_availability(availability)

    if smoke:
        return smoke_converse(region, model_id)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--company", required=True)
    parser.add_argument("--website", required=True)
    parser.add_argument("--use-case", required=True)
    parser.add_argument("--industry", default="Insurance")
    parser.add_argument(
        "--model",
        action="append",
        dest="models",
        help="Foundation model id; repeatable. Default: sonnet-5, opus-5-5, sonnet-4-6.",
    )
    parser.add_argument(
        "--no-smoke",
        action="store_true",
        help="Skip Converse smoke test (agreement-only).",
    )
    args = parser.parse_args(argv)
    models = args.models or list(DEFAULT_MODELS)

    bedrock = boto3.client("bedrock", region_name=args.region)
    try:
        put_use_case(bedrock, args.company, args.website, args.use_case, args.industry)
    except ClientError as exc:
        print(f"put_use_case_for_model_access failed: {exc}", file=sys.stderr)
        return 1

    smoke = not args.no_smoke
    failed = [m for m in models if not enable_model(bedrock, m, args.region, smoke=smoke)]
    if failed:
        print(f"\nFailed: {', '.join(failed)}", file=sys.stderr)
        print(
            "Note: agreement AVAILABLE can still fail Converse with "
            "'not available for this account' (sales/allowlist). That is not fixed by redeploy.",
            file=sys.stderr,
        )
        return 1
    print("\nDone. Marketplace agreement and Converse smoke succeeded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
