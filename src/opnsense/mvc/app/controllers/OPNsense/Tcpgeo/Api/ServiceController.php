<?php

/**
 * TCPGeo Service API Controller
 * Handles start/stop/restart/status, reconfigure, and GeoIP download.
 * Uses ApiControllerBase directly for maximum compatibility.
 * @package OPNsense\Tcpgeo
 */

namespace OPNsense\Tcpgeo\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\Backend;

class ServiceController extends ApiControllerBase
{
    /**
     * Reconfigure (generates config.json + restarts service)
     * @return array
     */
    public function reconfigureAction()
    {
        if ($this->request->isPost()) {
            session_write_close();
            $backend = new Backend();
            $response = trim($backend->configdRun('tcpgeo reconfigure'));
            return ['status' => 'ok', 'response' => $response];
        }
        return ['status' => 'failed'];
    }

    /**
     * Start the TCPGeo service
     * @return array
     */
    public function startAction()
    {
        if ($this->request->isPost()) {
            session_write_close();
            $backend = new Backend();
            $response = trim($backend->configdRun('tcpgeo start'));
            return ['status' => 'ok', 'response' => $response];
        }
        return ['status' => 'failed'];
    }

    /**
     * Stop the TCPGeo service
     * @return array
     */
    public function stopAction()
    {
        if ($this->request->isPost()) {
            session_write_close();
            $backend = new Backend();
            $response = trim($backend->configdRun('tcpgeo stop'));
            return ['status' => 'ok', 'response' => $response];
        }
        return ['status' => 'failed'];
    }

    /**
     * Restart the TCPGeo service
     * @return array
     */
    public function restartAction()
    {
        if ($this->request->isPost()) {
            session_write_close();
            $backend = new Backend();
            $response = trim($backend->configdRun('tcpgeo restart'));
            return ['status' => 'ok', 'response' => $response];
        }
        return ['status' => 'failed'];
    }

    /**
     * Get TCPGeo service status
     * @return array
     */
    public function statusAction()
    {
        $backend = new Backend();
        $response = trim($backend->configdRun('tcpgeo status'));
        if (strpos($response, 'running') !== false) {
            $status = 'running';
        } elseif (strpos($response, 'stopped') !== false) {
            $status = 'stopped';
        } else {
            $status = 'unknown';
        }
        return ['status' => $status, 'response' => $response];
    }

    /**
     * Download / update the GeoIP database
     * @return array
     */
    public function downloadgeoipAction()
    {
        if ($this->request->isPost()) {
            session_write_close();
            $backend = new Backend();
            $response = trim($backend->configdRun('tcpgeo download-geoip'));
            return ['status' => 'ok', 'response' => $response];
        }
        return ['status' => 'failed'];
    }
}
