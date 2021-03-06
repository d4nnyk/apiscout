########################################################################
# Copyright (c) 2017
# Daniel Plohmann <daniel.plohmann<at>mailbox<dot>org>
# All rights reserved.
########################################################################
#
#  This file is part of apiscout
#
#  apiscout is free software: you can redistribute it and/or modify it
#  under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful, but
#  WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#  General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see
#  <http://www.gnu.org/licenses/>.
#
########################################################################

import struct
import os
import json
import operator
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)-15s %(message)s")
LOG = logging.getLogger(__name__)


class ApiScout(object):

    def __init__(self, db_filepath=None):
        self.api_maps = {}
        self.has_64bit = False
        self.base_address = 0
        self.ignore_aslr_offsets = False
        if db_filepath:
            self.loadDbFile(db_filepath)

    def loadDbFile(self, db_filepath):
        api_db = {}
        if os.path.isfile(db_filepath):
            with open(db_filepath, "r") as f_json:
                api_db = json.loads(f_json.read())
        else:
            LOG.error("Not a file: %s!", db_filepath)
            raise ValueError
        num_apis_loaded = 0
        api_map = {}
        for dll_entry in api_db["dlls"]:
            LOG.debug("  building address map for: %s", dll_entry)
            aslr_offset = 0
            if not self.ignore_aslr_offsets:
                aslr_offset = api_db["dlls"][dll_entry]["aslr_offset"]
            for export in api_db["dlls"][dll_entry]["exports"]:
                num_apis_loaded += 1
                api_name = "%s" % (export["name"])
                dll_name = "_".join(dll_entry.split("_")[2:])
                bitness = api_db["dlls"][dll_entry]["bitness"]
                self.has_64bit |= bitness == 64
                base_address = api_db["dlls"][dll_entry]["base_address"]
                api_map[base_address + export["address"] - aslr_offset] = (dll_name, api_name, bitness)
            LOG.debug("loaded %d exports", num_apis_loaded)
        self.api_maps[api_db["os_name"]] = api_map

    def _resolveApiByAddress(self, api_map_name, absolute_addr):
        api_entry = ("", "", "")
        api_map = self.api_maps[api_map_name]
        check_address = absolute_addr
        if check_address in api_map:
            api_entry = api_map[check_address]
        return api_entry

    def getNumApisLoaded(self):
        return sum([len(self.api_maps[api_map_name]) for api_map_name in self.api_maps])

    def ignoreAslrOffsets(self, value):
        self.ignore_aslr_offsets = value

    def setBaseAddress(self, address):
        self.base_address = address

    def iterateAllDwords(self, binary):
        for offset, _ in enumerate(binary):
            try:
                dword = struct.unpack("I", binary[offset:offset + 4])[0]
                yield offset, dword
            except struct.error:
                break

    def iterateAllQwords(self, binary):
        for offset, _ in enumerate(binary):
            try:
                dword = struct.unpack("Q", binary[offset:offset + 8])[0]
                yield offset, dword
            except struct.error:
                break

    def crawl(self, binary):
        results = {}
        for api_map_name in self.api_maps:
            recovered_apis = []
            for offset, api_address in self.iterateAllDwords(binary):
                dll, api, bitness = self._resolveApiByAddress(api_map_name, api_address)
                if dll and api and bitness == 32:
                    recovered_apis.append((offset, api_address, dll, api, bitness))
            if self.has_64bit:
                for offset, api_address in self.iterateAllQwords(binary):
                    dll, api, bitness = self._resolveApiByAddress(api_map_name, api_address)
                    if dll and api and bitness == 64:
                        recovered_apis.append((offset, api_address, dll, api, bitness))
            results[api_map_name] = recovered_apis
        return results

    def filter(self, result, from_addr, to_addr, distance):
        filtered_result = {}
        for key in result:
            filtered_list = result[key]
            if from_addr:
                filtered_list = [item for item in filtered_list if self.base_address + item[0] >= from_addr]
            if to_addr:
                filtered_list = [item for item in filtered_list if self.base_address + item[0] <= to_addr]
            if distance:
                if len(filtered_list) < 2:
                    filtered_list = []
                else:
                    offsets_a = [item[0] for item in filtered_list]
                    offsets_b = offsets_a[1:] + [0]
                    api_distances = list(map(operator.sub, offsets_b, offsets_a))
                    distance_filtered = []
                    for index, api_distance in enumerate(api_distances[:-1]):
                        if api_distance <= distance:
                            if filtered_list[index] not in distance_filtered:
                                distance_filtered.append(filtered_list[index])
                            if filtered_list[index + 1] not in distance_filtered:
                                distance_filtered.append(filtered_list[index + 1])
                    filtered_list = distance_filtered
            filtered_result[key] = filtered_list
        return filtered_result

    def render(self, results):
        output = ""
        for api_map_name in results:
            if len(results[api_map_name]):
                result = results[api_map_name]
                output += "Results for API DB: {}\n".format(api_map_name)
                output += "{:3}: {:10}; {:18}; {:30}; {:60}\n".format("idx", "offset", "VA", "DLL", "API")
                prev_offset = 0
                dlls = set()
                apis = set()
                for index, entry in enumerate(result):
                    if prev_offset and entry[0] > prev_offset + 16:
                        output += "-" * 129 + "\n"
                    dll_name = "{} ({}bit)".format(entry[2], entry[4])
                    if entry[4] == 32:
                        output += "{:3}: 0x{:08x};         0x{:08x}; {:30}; {:60}\n".format(index + 1, self.base_address + entry[0], entry[1], dll_name, entry[3])
                    else:
                        output += "{:3}: 0x{:08x}; 0x{:016x}; {:30}; {:60}\n".format(index + 1, self.base_address + entry[0], entry[1], dll_name, entry[3])
                    prev_offset = entry[0]
                    dlls.add(entry[2])
                    apis.add(entry[3])
                output += "DLLs: {}, APIs: {}\n".format(len(dlls), len(apis))
            else:
                output += "No results for API map: {}\n".format(api_map_name)
        return output
