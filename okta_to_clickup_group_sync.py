import os
import json
import logging

import requests
from dotenv import load_dotenv

####################################################################################################
# Configuration
####################################################################################################

DRY_RUN = False
DEBUG = True

####################################################################################################
# Shouldn't need to change anything below this line
####################################################################################################

# Env Vars
load_dotenv()

OKTA_API_TOKEN = os.getenv("OKTA_API_TOKEN")
OKTA_BASE_URL = os.getenv("OKTA_BASE_URL")
OKTA_GROUP_PREFIXES = list(map(str.strip, os.getenv("OKTA_GROUP_PREFIXES").split(",")))
CLICKUP_API_TOKEN = os.getenv("CLICKUP_API_TOKEN")
CLICKUP_BASE_URL = os.getenv("CLICKUP_BASE_URL")
CLICKUP_TEAM_ID = os.getenv("CLICKUP_TEAM_ID")
EMAIL_DOMAIN = os.getenv("EMAIL_DOMAIN")

for env_var in ["OKTA_BASE_URL", "OKTA_GROUP_PREFIXES", "CLICKUP_BASE_URL", "CLICKUP_TEAM_ID", "EMAIL_DOMAIN"]:
    if os.getenv(env_var) is None or os.getenv(env_var) == "":
        raise NameError(f"Please set the {env_var} environment variable with the appropriate value in the .env file")

# Logging
logger = logging.getLogger('okta_to_clickup_group_sync')
logger.datefmt = '%Y-%m-%d %H:%M:%S'

logger.setLevel(logging.DEBUG)

# create file handler
fh = logging.FileHandler('okta_to_clickup_group_sync.log')
fh.setLevel(logging.DEBUG)

# create console handler
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)

# create formatter and add it to the handlers
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
ch.setFormatter(formatter)
fh.setFormatter(formatter)

# add the handlers to logger
logger.addHandler(ch)
logger.addHandler(fh)

log_separator = "----------------------------------------------------------"


def log_response(url, response):
    logger.info(log_separator)
    logger.info(f"Request URL: {url}")
    logger.info(f"Request Method: {response.request.method}")
    logger.info(f"Request Status Code: {response.status_code}")

    if DEBUG:
        logger.debug(json.dumps(response.json(), indent=2))


def log_output(output, func):
    if DEBUG:
        logger.debug(log_separator)
        logger.debug(f"Function: {func}")
        logger.debug(json.dumps(output, indent=2))


# Request Headers
okta_headers = {"Authorization": f"SSWS {OKTA_API_TOKEN}"}
clickup_headers = {"Authorization": CLICKUP_API_TOKEN}


def get_okta_groups():
    logger.info("Retrieving Okta Groups...")
    url = f"{OKTA_BASE_URL}/groups"

    response = requests.get(url, headers=okta_headers)
    log_response(url, response)

    okta_groups = response.json()

    group_members = {}
    for okta_group in okta_groups:
        if not okta_group["profile"]["name"].startswith(tuple(OKTA_GROUP_PREFIXES)):
            continue

        group_members[okta_group["profile"]["name"]] = []

        url = f"{OKTA_BASE_URL}/groups/{okta_group['id']}/users"

        response = requests.get(url, headers=okta_headers)
        log_response(url, response)

        data = response.json()

        group_members[okta_group["profile"]["name"]] = [user["profile"]["email"] for user in data if
                                                        user["profile"]["email"].endswith(EMAIL_DOMAIN)]

        log_output(group_members, "Get Okta Groups")

    return group_members


def get_clickup_users():
    url = f"{CLICKUP_BASE_URL}/team"
    params = {"id": CLICKUP_TEAM_ID}

    response = requests.get(url, headers=clickup_headers, params=params)
    log_response(url, response)

    clickup_users = response.json()["teams"][0]["members"]

    log_output(clickup_users, "Get ClickUp Users")

    return clickup_users


def get_clickup_groups():
    url = f"{CLICKUP_BASE_URL}/group"
    params = {"team_id": CLICKUP_TEAM_ID}

    response = requests.get(url, headers=clickup_headers, params=params)
    log_response(url, response)

    data = response.json()

    clickup_groups = {}
    for group in data["groups"]:
        if group["name"].startswith(tuple(OKTA_GROUP_PREFIXES)):
            if group["name"] not in clickup_groups:
                clickup_groups[group["name"]] = []
            clickup_groups[group["name"]] = {"id": group["id"],
                                             "members": [member["id"] for member in group["members"]]}

    log_output(clickup_groups, "Get ClickUp Groups")

    return clickup_groups


def match_users():
    okta = get_okta_groups()
    clickup = get_clickup_users()
    group_members = {}
    for group, emails in okta.items():
        for email in emails:
            for user in clickup:
                if user["user"]["email"] == email:
                    if group not in group_members:
                        group_members[group] = []
                    group_members[group].append(user["user"]["id"])

    log_output(group_members, "Match Users")

    return group_members


def sync_groups():
    okta_groups = match_users()
    clickup_groups = get_clickup_groups()

    for okta_group, okta_members in okta_groups.items():
        if okta_group not in clickup_groups.keys():
            url = f"{CLICKUP_BASE_URL}/team/{CLICKUP_TEAM_ID}/group"
            clickup_headers["Content-Type"] = "application/json"

            payload = {"name": okta_group, "members": okta_members}

            logger.info(log_separator)
            logger.info(f"Create group {okta_group} in ClickUp")
            logger.info(f"Payload: {payload}")

            if not DRY_RUN:
                response = requests.post(url, json=payload, headers=clickup_headers)
                log_response(url, response)
            else:
                logger.info(f"Request URL: {url}")
                logger.info("*** Dry Run, no changes made ***")

        for clickup_group, properties in clickup_groups.items():
            if okta_group == clickup_group:

                add = list(set(okta_members) - set(properties["members"]))
                rem = list(set(properties["members"]) - set(okta_members))

                url = f"{CLICKUP_BASE_URL}/group/{properties['id']}"
                clickup_headers["Content-Type"] = "application/json"

                payload = {}

                if len(add) > 0:
                    payload["members"] = dict(add=add)
                if len(rem) > 0:
                    if "members" in payload:
                        payload["members"].update({"rem": rem})
                    else:
                        payload["members"] = dict(rem=rem)

                if len(add) > 0 or len(rem) > 0:
                    logger.info(log_separator)
                    logger.info(f"Update group {clickup_group} in ClickUp")
                    if len(add) > 0:
                        logger.info(f"Missing users to be created: {add}")
                    if len(rem) > 0:
                        logger.info(f"Users to be removed: {rem}")
                    logger.info(f"Payload: {payload}")

                    if not DRY_RUN:
                        response = requests.put(url, json=payload, headers=clickup_headers)
                        log_response(url, response)
                    else:
                        logger.info(f"Request URL: {url}")
                        logger.info("*** Dry Run, no changes made ***")

                else:
                    logger.info(log_separator)
                    logger.info(f"No changes required for group {clickup_group} in ClickUp")


if __name__ == "__main__":
    sync_groups()
