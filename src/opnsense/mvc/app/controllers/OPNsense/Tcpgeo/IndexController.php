<?php

/**
 * TCPGeo Index Controller – UI Settings Page
 * @package OPNsense\Tcpgeo
 */

namespace OPNsense\Tcpgeo;

class IndexController extends \OPNsense\Base\IndexController
{
    /**
     * Renders the TCPGeo settings page
     */
    public function indexAction()
    {
        $this->view->title = gettext('TCPGeo');
        $this->view->generalForm = $this->getForm('general');
        $this->view->portcolorForm = $this->getForm('dialogPortcolor');
        $this->view->pick('OPNsense/Tcpgeo/index');
    }
}
