"""Gauge watcher implementations."""

# Copyright (C) 2013 Nippon Telegraph and Telephone Corporation.
# Copyright (C) 2015 Brad Cowie, Christopher Lorier and Joe Stringer.
# Copyright (C) 2015 Research and Education Advanced Network New Zealand Ltd.
# Copyright (C) 2015--2019 The Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import gzip

from ryu.ofproto import ofproto_v1_3 as ofp

from faucet.conf import InvalidConfigError
from faucet.valve_util import dpid_log
from faucet.gauge_influx import (
    GaugePortStateInfluxDBLogger, GaugePortStatsInfluxDBLogger, GaugeFlowTableInfluxDBLogger)
from faucet.gauge_pollers import (
    GaugePortStatePoller, GaugePortStatsPoller, GaugeFlowTablePoller, GaugeMeterStatsPoller)
from faucet.gauge_prom import (
    GaugePortStatsPrometheusPoller, GaugePortStatePrometheusPoller, GaugeFlowTablePrometheusPoller,
    GaugeMeterStatsPrometheusPoller)


def watcher_factory(conf):
    """Return a Gauge object based on type.

    Args:
        conf (GaugeConf): object with the configuration for this valve.
    """

    WATCHER_TYPES = {
        'port_state': {
            'text': GaugePortStateLogger,
            'influx': GaugePortStateInfluxDBLogger,
            'prometheus': GaugePortStatePrometheusPoller,
            },
        'port_stats': {
            'text': GaugePortStatsLogger,
            'influx': GaugePortStatsInfluxDBLogger,
            'prometheus': GaugePortStatsPrometheusPoller,
            },
        'flow_table': {
            'text': GaugeFlowTableLogger,
            'influx': GaugeFlowTableInfluxDBLogger,
            'prometheus': GaugeFlowTablePrometheusPoller,
            },
        'meter_stats': {
            'text': GaugeMeterStatsLogger,
            'prometheus': GaugeMeterStatsPrometheusPoller,
            },
    }

    w_type = conf.type
    db_type = conf.db_type
    try:
        return WATCHER_TYPES[w_type][db_type]
    except KeyError:
        raise InvalidConfigError('invalid water config')


class GaugePortStateLogger(GaugePortStatePoller):
    """Abstraction for port state logger."""

    def _update(self, rcv_time, msg):
        rcv_time_str = self._rcv_time(rcv_time)
        reason = msg.reason
        port_no = msg.desc.port_no
        log_msg = 'port %s unknown state %s' % (port_no, reason)
        if reason == ofp.OFPPR_ADD:
            log_msg = 'port %s added' % port_no
        elif reason == ofp.OFPPR_DELETE:
            log_msg = 'port %s deleted' % port_no
        elif reason == ofp.OFPPR_MODIFY:
            link_down = (msg.desc.state & ofp.OFPPS_LINK_DOWN)
            if link_down:
                log_msg = 'port %s down' % port_no
            else:
                log_msg = 'port %s up' % port_no
        log_msg = '%s %s' % (dpid_log(self.dp.dp_id), log_msg)
        self.logger.info(log_msg)
        if self.conf.file:
            with open(self.conf.file, 'a') as logfile:
                logfile.write('\t'.join((rcv_time_str, log_msg)) + '\n')

    @staticmethod
    def send_req():
        """Send a stats request to a datapath."""
        raise NotImplementedError # pragma: no cover

    @staticmethod
    def no_response():
        """Called when a polling cycle passes without receiving a response."""
        raise NotImplementedError # pragma: no cover


class GaugePortStatsLogger(GaugePortStatsPoller):
    """Abstraction for port statistics logger."""

    def _dp_stat_name(self, stat, stat_name):  # pylint: disable=arguments-differ
        port_name = self.dp.port_labels(stat.port_no)['port']
        return '-'.join((self.dp.name, port_name, stat_name))


class GaugeMeterStatsLogger(GaugeMeterStatsPoller):
    """Abstraction for meter statistics logger."""

    def _format_stat_pairs(self, delim, stat):
        band_stats = stat.band_stats[0]
        stat_pairs = (
            (('flow', 'count'), stat.flow_count),
            (('byte', 'in', 'count'), stat.byte_in_count),
            (('packet', 'in', 'count'), stat.packet_in_count),
            (('byte', 'band', 'count'), band_stats.byte_band_count),
            (('packet', 'band', 'count'), band_stats.packet_band_count))
        return self._format_stats(delim, stat_pairs)

    def _dp_stat_name(self, stat, stat_name):  # pylint: disable=arguments-differ
        return '-'.join((self.dp.name, str(stat.meter_id), stat_name))


class GaugeFlowTableLogger(GaugeFlowTablePoller):
    """Periodically dumps the current datapath flow table as a yaml object.

    Includes a timestamp and a reference ($DATAPATHNAME-flowtables). The
    flow table is dumped as an OFFlowStatsReply message (in yaml format) that
    matches all flows.

    optionally the output can be compressed by setting compressed: true in the
    config for this watcher
    """

    def _update(self, rcv_time, msg):
        # TODO: it might be good to aggregate all OFFlowStatsReplies somehow
        rcv_time_str = self._rcv_time(rcv_time)
        jsondict = {
            'time': rcv_time_str,
            'ref': '-'.join((self.dp.name, 'flowtables')),
            'msg': msg.to_jsondict()}
        filename = self.conf.file
        outstr = '---\n{}\n'.format(json.dumps(jsondict))
        if self.conf.compress:
            with gzip.open(filename, 'at') as outfile:
                outfile.write(outstr)
        else:
            with open(filename, 'a') as outfile:
                outfile.write(outstr)
