<?php

/**
 * TCPGeo OPNsense Plugin - plugins.inc.d integration
 * Provides service hooks, cron jobs, and syslog registration.
 */

use OPNsense\Core\Config;

/**
 * Register TCPGeo services for the Dashboard / Service overview
 */
function tcpgeo_services()
{
    $services = [];

    $mdl = new \OPNsense\Tcpgeo\Tcpgeo();
    if ((string)$mdl->general->enabled === '1') {
        $services[] = [
            'description' => gettext('TCPGeo Live Traffic Globe'),
            'configd' => [
                'restart' => ['tcpgeo restart'],
                'start'   => ['tcpgeo start'],
                'stop'    => ['tcpgeo stop'],
            ],
            'name'   => 'tcpgeo',
            'pidfile' => '/var/run/tcpgeo.pid',
        ];
    }

    return $services;
}

/**
 * Generate cron jobs for GeoIP auto-update (weekly)
 */
function tcpgeo_cron()
{
    $jobs = [];

    $mdl = new \OPNsense\Tcpgeo\Tcpgeo();
    if ((string)$mdl->general->enabled === '1' && !empty((string)$mdl->general->maxmindkey)) {
        $jobs[] = [
            'minutes'     => '30',
            'hours'       => '3',
            'days'        => '*',
            'months'      => '*',
            'weekdays'    => '0',   // Sunday at 03:30
            'description' => 'TCPGeo GeoIP database update',
            'command'     => '/usr/local/bin/python3 /usr/local/opnsense/scripts/tcpgeo/download_geoip.py',
        ];
    }

    return $jobs;
}

/**
 * Register syslog facility
 */
function tcpgeo_syslog()
{
    return [
        'tcpgeo' => ['facility' => ['tcpgeo']],
    ];
}
