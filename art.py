"""
SamsungTVWS - Samsung Smart TV WS API wrapper

Copyright (C) 2019 DSR! <xchwarze@gmail.com>
Copyright (C) 2021 Matthew Garrett <mjg59@srcf.ucam.org>
Copyright (C) 2024 Nick Waterton <n.waterton@outlook.com>

SPDX-License-Identifier: LGPL-3.0
"""

from datetime import datetime
import os
import json
import logging
import random
import socket
from typing import Any, Dict, List, Optional, Union
import uuid

import websocket

from . import exceptions, helper
from .command import SamsungTVCommand
from .connection import SamsungTVWSConnection
from .event import D2D_SERVICE_MESSAGE_EVENT, MS_CHANNEL_READY_EVENT
from .rest import SamsungTVRest
from .helper import get_ssl_context

_LOGGING = logging.getLogger(__name__)

ART_ENDPOINT = "com.samsung.art-app"


class ArtChannelEmitCommand(SamsungTVCommand):
    def __init__(self, params: Dict[str, Any]) -> None:
        super().__init__("ms.channel.emit", params)

    @staticmethod
    def art_app_request(data: Dict[str, Any]) -> "ArtChannelEmitCommand":
        return ArtChannelEmitCommand(
            {
                "event": "art_app_request",
                "to": "host",
                "data": json.dumps(data),
            }
        )


class SamsungTVArt(SamsungTVWSConnection):
    def __init__(
        self,
        host,
        token=None,
        token_file=None,
        port=8001,
        timeout=None,
        key_press_delay=1,
        name="SamsungTvRemote",
    ):
        super().__init__(
            host,
            endpoint=ART_ENDPOINT,
            token=token,
            token_file=token_file,
            port=port,
            timeout=timeout,
            key_press_delay=key_press_delay,
            name=name,
        )
        self.art_uuid = str(uuid.uuid4())
        self._rest_api: Optional[SamsungTVRest] = None
        self.pending_requests = None

    def open(self) -> websocket.WebSocket:
        super().open()

        # Override base class to wait for MS_CHANNEL_READY_EVENT
        assert self.connection
        data = self.connection.recv()
        response = helper.process_api_response(data)
        event = response.get("event", "*")
        self._websocket_event(event, response)

        if event != MS_CHANNEL_READY_EVENT:
            self.close()
            raise exceptions.ConnectionFailure(response)

        return self.connection
        
    def get_uuid(self):
        self.art_uuid = str(uuid.uuid4())
        return self.art_uuid
        
    def get_websocket_message(self):
        try:
            raw_data = self.connection.recv()
            response = helper.process_api_response(raw_data)
            event = response.get("event", "*")
            self._websocket_event(event, response)
            _LOGGING.debug('event: {}'.format(event))
            if event == D2D_SERVICE_MESSAGE_EVENT:
                return json.loads(response["data"])
        except websocket.WebSocketTimeoutException as e:
            raise exceptions.TimeoutError('Websocket Time out: {}'.format(e))
        return {}
        
    def wait_for_response(self, wait_for_event, request_uuid=None):
        while True:
            data = self.get_websocket_message()
            _LOGGING.debug('request_uuid: {}, message uuid: {}'.format(request_uuid, data.get('request_id', data.get('id'))))
            if data.get('request_id', data.get('id')) == request_uuid:
                sub_event = data.get("event", "*")
                _LOGGING.debug('sub_event: {}, wait_for_event: {}'.format(sub_event, wait_for_event))
                if sub_event == "error":
                    raise exceptions.ResponseError(
                        f"{json.loads(data['request_data'])['request']} request failed "
                        f"with error number {data['error_code']}"
                    )
                # Check sub event, return if found or not defined
                if not wait_for_event or sub_event == wait_for_event:
                    return data
        return None

    def _send_art_request(
        self,
        request_data: Dict[str, Any],
        wait_for_event: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not request_data.get("id"):
            request_data["id"] = self.get_uuid()            #old api
        request_data["request_id"] = request_data["id"]     #new api  
        self.send_command(ArtChannelEmitCommand.art_app_request(request_data))
        return self.wait_for_response(wait_for_event, request_data["id"])

    def _get_rest_api(self) -> SamsungTVRest:
        if self._rest_api is None:
            self._rest_api = SamsungTVRest(self.host, self.port, self.timeout)
        return self._rest_api

    def supported(self) -> bool:
        data = self._get_rest_api().rest_device_info()
        return data.get("device", {}).get("FrameTVSupport") == "true"

    def get_api_version(self):
        try:
            data = self._send_art_request(
                #new api, throws ResponseError on old tv's
                {"request": "api_version"}
            )
        except exceptions.ResponseError:
            data = self._send_art_request(
                #old api produces no response on new TV's
                {"request": "get_api_version"}
            )
        assert data
        return data["version"]

    def get_device_info(self):
        data = self._send_art_request(
            {"request": "get_device_info"}
        )
        assert data
        return data

    def available(self, category=None):
        data = self._send_art_request(
            {"request": "get_content_list", "category": category}
        )
        assert data
        return [ v for v in json.loads(data["content_list"]) if v['category_id'] == category] if category else json.loads(data["content_list"])

    def get_current(self):
        data = self._send_art_request(
            {"request": "get_current_artwork"}
        )
        assert data
        return data
        
    def set_favourite(self, content_id, status='on'):
        data = self._send_art_request(
            {   "request": "change_favorite",
                "content_id": content_id,
                "status": status},
            wait_for_event="favorite_changed"
        )
        assert data
        return data
        
    def get_artmode_settings(self, setting=''):
        '''
        setting can be any of 'brightness', 'color_temperature', 'motion_sensitivity',
        'motion_timer', or 'brightness_sensor_setting'
        '''
        data = self._send_art_request(
            {"request": "get_artmode_settings"}
        )
        assert data
        if 'data' in data.keys():
            data = json.loads(data["data"])
            return next(iter(item for item in data if item['item'] == setting), data)
        return data

    def get_auto_rotation_status(self):
        data = self._send_art_request(
            {"request": "get_auto_rotation_status"}
        )
        assert data
        return data
 
    def set_auto_rotation_status(self, duration=0, type=True, category=2):
        '''
        duration is "off" or "number" where number is duration in minutes. set 0 for 'off'
        slide show type can be "slideshow" or "shuffleslideshow", set True for shuffleslideshow
        category is 'MY-C0004' or 'MY-C0002' where 4 is favourites, 2 is my pictures, and 8 is store
        '''
        data = self._send_art_request(
            {"request": "set_auto_rotation_status", "value": str(duration) if duration > 0 else "off", "category_id": "MY-C000{}".format(category), "type": "shuffleslideshow" if type else "slideshow"}
        )
        assert data
        return data

    def get_slideshow_status(self):
        data = self._send_art_request(
            {"request": "get_slideshow_status"}
        )
        assert data
        return data

    def set_slideshow_status(self, duration=0, type=True, category=2):
        '''
        duration is "off" or "number" where number is duration in minutes. set 0 for 'off'
        slide show type can be "slideshow" or "shuffleslideshow", set True for shuffleslideshow
        category is 'MY-C0004' or 'MY-C0002' where 4 is favourites, 2 is my pictures, and 8 is store
        '''
        data = self._send_art_request(
            {"request": "set_slideshow_status", "value": str(duration) if duration > 0 else "off", "category_id": "MY-C000{}".format(category), "type": "shuffleslideshow" if type else "slideshow"}
        )
        assert data
        return data

    def get_brightness(self):
        try:
            data = self.get_artmode_settings('brightness')
        except exceptions.ResponseError:
            data = self._send_art_request(
                {"request": "get_brightness"}
            )
        assert data
        return data['value']

    def set_brightness(self, value):
        data = self._send_art_request(
            {"request": "set_brightness", "value": value}
        )
        assert data
        return data
        
    def get_color_temperature(self):
        try:
            data = self.get_artmode_settings('color_temperature')
        except exceptions.ResponseError:
            data = self._send_art_request(
                {"request": "get_color_temperature"}
            )
        assert data
        return data['value']

    def set_color_temperature(self, value):
        data = self._send_art_request(
            {"request": "set_color_temperature", "value": value}
        )
        assert data
        return data
 
    def get_thumbnail_list(self, content_id_list=[]):
        if isinstance(content_id_list, str):
            content_id_list=[content_id_list]
        content_id_list=[{"content_id": id} for id in content_id_list]
        data = self._send_art_request(
            {
                "request": "get_thumbnail_list",
                "content_id_list": content_id_list,
                "conn_info": {
                    "d2d_mode": "socket",
                    "connection_id": random.randrange(4 * 1024 * 1024 * 1024),
                    "id": self.get_uuid(),
                },
            }
        )
        assert data
        conn_info = json.loads(data["conn_info"])
        art_socket_raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        art_socket = get_ssl_context().wrap_socket(art_socket_raw) if conn_info.get('secured', False) else art_socket_raw
        art_socket.connect((conn_info["ip"], int(conn_info["port"])))
        total_num_thumbnails = 1
        current_thumb = -1
        thumbnail_data_dict = {}
        while current_thumb+1 < total_num_thumbnails:
            header_len = int.from_bytes(art_socket.recv(4), "big")
            header = json.loads(art_socket.recv(header_len))
            thumbnail_data_len = int(header["fileLength"])
            current_thumb = int(header["num"])
            total_num_thumbnails = int(header["total"])
            filename = "{}.{}".format(header["fileID"], header["fileType"])
            thumbnail_data = bytearray()
            while len(thumbnail_data) < thumbnail_data_len:
                packet = art_socket.recv(thumbnail_data_len - len(thumbnail_data))
                thumbnail_data.extend(packet)
            thumbnail_data_dict[filename]=thumbnail_data
        return thumbnail_data_dict

    def get_thumbnail(self, content_id_list=[], as_dict=False):
        if isinstance(content_id_list, str):
            content_id_list=[content_id_list]
        thumbnail_data_dict = {}
        thumbnail_data = None
        for content_id in content_id_list:
            data = self._send_art_request(
                {
                    "request": "get_thumbnail",
                    "content_id": content_id,
                    "conn_info": {
                        "d2d_mode": "socket",
                        "connection_id": random.randrange(4 * 1024 * 1024 * 1024),
                        "id": self.get_uuid(),
                    },
                }
            )
            assert data
            conn_info = json.loads(data["conn_info"])

            art_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            art_socket.connect((conn_info["ip"], int(conn_info["port"])))
            header_len = int.from_bytes(art_socket.recv(4), "big")
            header = json.loads(art_socket.recv(header_len))

            thumbnail_data_len = int(header["fileLength"])
            thumbnail_data = bytearray()
            while len(thumbnail_data) < thumbnail_data_len:
                packet = art_socket.recv(thumbnail_data_len - len(thumbnail_data))
                thumbnail_data.extend(packet)
            filename = "{}.{}".format(header["fileID"], header["fileType"])
            thumbnail_data_dict[filename] = thumbnail_data

        return thumbnail_data_dict if as_dict else list(thumbnail_data_dict.values()) if len(content_id_list) > 1 else thumbnail_data

    def upload(self, file, matte="shadowbox_polar", portrait_matte="shadowbox_polar", file_type="png", date=None):
        if isinstance(file, str):
            file_name, file_extension = os.path.splitext(file)
            file_type = file_extension[1:]
            with open(file, 'rb') as f:
                file = f.read()
                
        file_size = len(file)
        file_type = file_type.lower()
        if file_type == "jpeg":
            file_type = "jpg"

        if date is None:
            date = datetime.now().strftime("%Y:%m:%d %H:%M:%S")

        data = self._send_art_request(
            {
                "request": "send_image",
                "file_type": file_type,
                "request_id" : self.get_uuid(),
                "id": self.art_uuid,
                "conn_info": {
                    "d2d_mode": "socket",
                    "connection_id": random.randrange(4 * 1024 * 1024 * 1024),
                    "id": self.art_uuid,
                },
                "image_date": date,
                "matte_id": matte or 'none',
                "portrait_matte_id": portrait_matte or 'none',
                "file_size": file_size,
            },
            wait_for_event="ready_to_use"
        )
        assert data
        conn_info = json.loads(data["conn_info"])
        header = json.dumps(
            {
                "num": 0,
                "total": 1,
                "fileLength": file_size,
                "fileName": "dummy",
                "fileType": file_type,
                "secKey": conn_info["key"],
                "version": "0.0.1",
            }
        )

        art_socket_raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        art_socket = get_ssl_context().wrap_socket(art_socket_raw) if conn_info.get('secured', False) else art_socket_raw  
        art_socket.connect((conn_info["ip"], int(conn_info["port"])))
        art_socket.send(len(header).to_bytes(4, "big"))
        art_socket.send(header.encode("ascii"))
        art_socket.send(file)
        #_LOGGING.info('sending: header length: {}, header: {}'.format(len(header).to_bytes(4, "big").hex(), header.encode("ascii")))

        data = self.wait_for_response("image_added")
        return data["content_id"] if data else None

    def delete(self, content_id):
        return self.delete_list([content_id])

    def delete_list(self, content_ids):
        content_id_list = []
        for item in content_ids:
            content_id_list.append({"content_id": item})

        data = self._send_art_request(
            {"request": "delete_image_list", "content_id_list": content_id_list}
        )
        assert data
        return content_id_list == json.loads(data['content_id_list'])

    def select_image(self, content_id, category=None, show=True):
        self._send_art_request(
            {
                "request": "select_image",
                "category_id": category,
                "content_id": content_id,
                "show": show,
            }
        )

    def get_artmode(self):
        data = self._send_art_request(
            {
                "request": "get_artmode_status",
            }
        )
        assert data
        return data["value"]

    def set_artmode(self, mode):
        self._send_art_request(
            {
                "request": "set_artmode_status",
                "value": mode,
            }
        )
        
    def get_rotation(self):
        data = self._send_art_request(
            {"request": "get_current_rotation"}
        )
        assert data
        return data.get("current_rotation_status",0)

    def get_photo_filter_list(self):
        data = self._send_art_request(
            {"request": "get_photo_filter_list"}
        )
        assert data
        return json.loads(data["filter_list"])

    def set_photo_filter(self, content_id, filter_id):
        self._send_art_request(
            {
                "request": "set_photo_filter",
                "content_id": content_id,
                "filter_id": filter_id,
            }
        )

    def get_matte_list(self, include_colour=False):
        data = self._send_art_request(
            {"request": "get_matte_list"}
        )
        assert data
        return (json.loads(data["matte_type_list"]), json.loads(data.get("matte_color_list"))) if include_colour else json.loads(data["matte_type_list"])

    def change_matte(self, content_id, matte_id=None, portrait_matte=None):
        '''
        matte is name_color eg flexible_polar or none
        NOTE: Not all mattes can be set for all image sizes!
        '''
        art_request = {
                        "request": "change_matte",
                        "content_id": content_id,
                        "matte_id": matte_id or 'none',
                      }
        if portrait_matte:
            art_request["portrait_matte_id"] = portrait_matte
        self._send_art_request(art_request)
