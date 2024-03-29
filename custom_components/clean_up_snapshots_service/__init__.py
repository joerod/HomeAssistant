"""
Support for automating the deletion of snapshots.
"""
import logging

import pytz
from dateutil.parser import parse
import asyncio
import aiohttp
import async_timeout
from urllib.parse import urlparse

from homeassistant.const import (CONF_HOST, CONF_TOKEN)
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'clean_up_snapshots_service'
ATTR_NAME = 'number_of_snapshots_to_keep'
USE_SSL_IP = 'use_ssl_with_ip_addres'
DEFAULT_NUM = 0
BACKUPS_URL_PATH='backups'

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_HOST): cv.string,
        vol.Required(CONF_TOKEN): cv.string,
        vol.Optional(ATTR_NAME, default=DEFAULT_NUM): int,
        vol.Optional(USE_SSL_IP, default=False): cv.boolean
    }),
}, extra=vol.ALLOW_EXTRA)

async def async_setup(hass, config):
    conf = config[DOMAIN]
    base_url = conf.get(CONF_HOST)
    api_path = 'api/hassio/'
    hassio_url = f"{base_url}/{api_path}" if (base_url[-1] != '/') else f"{base_url}{api_path}"
    auth_token = conf.get(CONF_TOKEN)
    num_snapshots_to_keep = conf.get(ATTR_NAME, DEFAULT_NUM)
    use_ssl_with_ip = conf.get(USE_SSL_IP, False)
    headers = {'authorization': "Bearer {}".format(auth_token)}

    async def async_get_snapshots():
        _LOGGER.info('Calling get snapshots')
        async with aiohttp.ClientSession(raise_for_status=True) as session:
            try:
                with async_timeout.timeout(10):
                    resp = await session.get(hassio_url + BACKUPS_URL_PATH, headers=headers, ssl=shouldVerifySsl(hassio_url))
                data = await resp.json()
                await session.close()
                return data['data']['backups']
            except aiohttp.ClientError:
                _LOGGER.error("Client error on calling get snapshots", exc_info=True)
                await session.close()
            except asyncio.TimeoutError:
                _LOGGER.error("Client timeout error on get snapshots", exc_info=True)
                await session.close()
            except Exception:
                _LOGGER.error("Unknown exception thrown", exc_info=True)
                await session.close()

    async def async_remove_snapshots(stale_snapshots):
        for snapshot in stale_snapshots:
            async with aiohttp.ClientSession(raise_for_status=True) as session:
                _LOGGER.info('Attempting to remove snapshot: slug=%s', snapshot['slug'])
                # call hassio API deletion
                try:
                    with async_timeout.timeout(10):
                        resp = await session.delete(hassio_url + f"{BACKUPS_URL_PATH}/" + snapshot['slug'], headers=headers, ssl=shouldVerifySsl(hassio_url))
                    res = await resp.json()
                    if res['result'].lower() == "ok":
                        _LOGGER.info("Deleted snapshot %s", snapshot["slug"])
                        await session.close()
                        continue
                    else:
                        # log an error
                        _LOGGER.warning("Failed to delete snapshot %s: %s", snapshot["slug"], str(res.status_code))

                except aiohttp.ClientError:
                    _LOGGER.error("Client error on calling delete snapshot", exc_info=True)
                    await session.close()
                except asyncio.TimeoutError:
                    _LOGGER.error("Client timeout error on delete snapshot", exc_info=True)
                    await session.close()
                except Exception:
                    _LOGGER.error("Unknown exception thrown on calling delete snapshot", exc_info=True)
                    await session.close()

    def shouldVerifySsl(url):
        '''
        This method is used to determine if we want to verify SSL certs
        https://docs.aiohttp.org/en/stable/client_advanced.html#ssl-control-for-tcp-sockets
        By default if you are using an IP Address we will not verify the SSL request.
        This can be overriden using the use_ssl_with_ip_address in the configuration for this component.
        '''
        urlPieces = urlparse(url)

        # Check if the url is an IP Address
        if (isgoodipv4(urlPieces.netloc) and not use_ssl_with_ip):
            return False

        return urlPieces.scheme == "https"


    def isgoodipv4(s):
        if ':' in s:
            s = s.split(':')[0]
        pieces = s.split('.')
        if len(pieces) != 4:
            return False
        try:
            return all(0<=int(p)<256 for p in pieces)
        except ValueError:
            return False

    async def async_handle_clean_up(call):
        # Allow the service call override the configuration.
        num_to_keep = call.data.get(ATTR_NAME, num_snapshots_to_keep)
        _LOGGER.info('Number of snapshots we are going to keep: %s', str(num_to_keep))

        if num_to_keep == 0:
            _LOGGER.info('Number of snapshots to keep was zero which is default so no snapshots will be removed')
            return

        snapshots = await async_get_snapshots()
        _LOGGER.info('Snapshots: %s', snapshots)

        # filter the snapshots
        if snapshots is not None:
            for snapshot in snapshots:
                d = parse(snapshot["date"])
                if d.tzinfo is None or d.tzinfo.utcoffset(d) is None:
                    _LOGGER.info("Naive DateTime found for snapshot %s, setting to UTC...", snapshot["slug"])
                    snapshot["date"] = d.replace(tzinfo=pytz.utc).isoformat()
            snapshots.sort(key=lambda item: parse(item["date"]), reverse=True)
            stale_snapshots = snapshots[num_to_keep:]
            _LOGGER.info('Stale Snapshots: {}'.format(stale_snapshots))
            await async_remove_snapshots(stale_snapshots)
        else:
            _LOGGER.info('No snapshots found.')

    hass.services.async_register(DOMAIN, 'clean_up', async_handle_clean_up)

    return True
