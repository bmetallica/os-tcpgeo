<?php

/**
 * TCPGeo Settings API Controller
 * Handles CRUD for general settings and port-color mappings
 * @package OPNsense\Tcpgeo
 */

namespace OPNsense\Tcpgeo\Api;

use OPNsense\Base\ApiMutableModelControllerBase;
use OPNsense\Core\Config;

class SettingsController extends ApiMutableModelControllerBase
{
    protected static $internalModelName = 'tcpgeo';
    protected static $internalModelClass = 'OPNsense\Tcpgeo\Tcpgeo';

    /**
     * Retrieve general settings
     * Uses direct model access since 'general' is a ContainerField,
     * not an ArrayField (getBase calls Add() which only exists on ArrayField).
     * @return array
     */
    public function getAction()
    {
        $mdl = $this->getModel();
        return ['general' => $mdl->general->getNodes()];
    }

    /**
     * Update general settings
     * @return array
     */
    public function setAction()
    {
        $result = ['result' => 'failed'];
        if ($this->request->isPost()) {
            $mdl = $this->getModel();
            $post = $this->request->getPost('general');
            if (is_array($post)) {
                // setNodes on the general container directly (not model root)
                $mdl->general->setNodes($post);
                $valMsgs = $mdl->performValidation();
                foreach ($valMsgs as $msg) {
                    if (!array_key_exists('validations', $result)) {
                        $result['validations'] = [];
                    }
                    $result['validations']['general.' . $msg->getField()] = $msg->getMessage();
                }
                if (empty($result['validations'])) {
                    $mdl->serializeToConfig();
                    Config::getInstance()->save();
                    $result['result'] = 'saved';
                }
            }
        }
        return $result;
    }

    /**
     * Search port-color entries
     * @return array
     */
    public function searchPortcolorAction()
    {
        return $this->searchBase(
            'portcolors.portcolor',
            ['enabled', 'port', 'color', 'label'],
            'port'
        );
    }

    /**
     * Get a single port-color entry by UUID
     * @param string $uuid
     * @return array
     */
    public function getPortcolorAction($uuid = null)
    {
        return $this->getBase('portcolor', 'portcolors.portcolor', $uuid);
    }

    /**
     * Add a new port-color entry
     * @return array
     */
    public function addPortcolorAction()
    {
        return $this->addBase('portcolor', 'portcolors.portcolor');
    }

    /**
     * Update a port-color entry by UUID
     * @param string $uuid
     * @return array
     */
    public function setPortcolorAction($uuid)
    {
        return $this->setBase('portcolor', 'portcolors.portcolor', $uuid);
    }

    /**
     * Delete a port-color entry by UUID
     * @param string $uuid
     * @return array
     */
    public function delPortcolorAction($uuid)
    {
        return $this->delBase('portcolors.portcolor', $uuid);
    }

    /**
     * Toggle enabled state of a port-color entry
     * @param string $uuid
     * @param string $enabled
     * @return array
     */
    public function togglePortcolorAction($uuid, $enabled = null)
    {
        return $this->toggleBase('portcolors.portcolor', $uuid, $enabled);
    }
}
