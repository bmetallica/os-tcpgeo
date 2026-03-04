{#
    TCPGeo Settings Page
    OPNsense MVC Volt Template
#}

<script>
    $( document ).ready(function() {
        // Load general settings into form
        var data_get_map = {'frm_GeneralSettings': "/api/tcpgeo/settings/get"};
        mapDataToFormUI(data_get_map).done(function(data){
            formatTokenizersUI();
            $('.selectpicker').selectpicker('refresh');
        });

        // Port-Color Bootgrid
        $("#grid-portcolors").UIBootgrid({
            search: '/api/tcpgeo/settings/searchPortcolor',
            get: '/api/tcpgeo/settings/getPortcolor/',
            set: '/api/tcpgeo/settings/setPortcolor/',
            add: '/api/tcpgeo/settings/addPortcolor/',
            del: '/api/tcpgeo/settings/delPortcolor/',
            toggle: '/api/tcpgeo/settings/togglePortcolor/',
            options: {
                formatters: {
                    "commands": function(column, row) {
                        return '<button type="button" class="btn btn-xs btn-default command-edit bootgrid-tooltip" data-row-id="' + row.uuid + '"><span class="fa fa-fw fa-pencil"></span></button> ' +
                               '<button type="button" class="btn btn-xs btn-default command-copy bootgrid-tooltip" data-row-id="' + row.uuid + '"><span class="fa fa-fw fa-clone"></span></button> ' +
                               '<button type="button" class="btn btn-xs btn-default command-delete bootgrid-tooltip" data-row-id="' + row.uuid + '"><span class="fa fa-fw fa-trash-o"></span></button>';
                    },
                    "colorpreview": function(column, row) {
                        var safeColor = /^#[0-9a-fA-F]{6}$/.test(row.color) ? row.color : '#cccccc';
                        return '<span style="display:inline-block;width:20px;height:20px;background:' + safeColor + ';border:1px solid #ccc;border-radius:3px;vertical-align:middle;margin-right:5px;"></span> ' + $('<span>').text(safeColor)[0].innerHTML;
                    },
                    "rowtoggle": function(column, row) {
                        if (parseInt(row[column.id], 2) === 1) {
                            return '<span class="fa fa-fw fa-check-square-o command-toggle bootgrid-tooltip" data-value="1" data-row-id="' + row.uuid + '"></span>';
                        } else {
                            return '<span class="fa fa-fw fa-square-o command-toggle bootgrid-tooltip" data-value="0" data-row-id="' + row.uuid + '"></span>';
                        }
                    }
                }
            }
        });

        // Save & Apply
        $("#saveAct").SimpleActionButton({
            onPreAction: function() {
                const dfObj = new $.Deferred();
                saveFormToEndpoint("/api/tcpgeo/settings/set", 'frm_GeneralSettings', function(){
                    dfObj.resolve();
                }, true, function(){
                    dfObj.reject();
                });
                return dfObj;
            },
            onAction: function(data, status) {
                ajaxCall("/api/tcpgeo/service/reconfigure", {}, function(data, status) {
                    updateServiceControlUI('tcpgeo');
                });
            }
        });

        // Service control buttons
        updateServiceControlUI('tcpgeo');

        // GeoIP download button
        $("#downloadGeoIP").click(function(){
            var btn = $(this);
            btn.prop('disabled', true).html('<i class="fa fa-spinner fa-spin"></i> Lade...');
            ajaxCall("/api/tcpgeo/service/downloadgeoip", {}, function(data, status) {
                btn.prop('disabled', false).html('<i class="fa fa-download"></i> GeoIP aktualisieren');
                if (data && data.status === 'ok') {
                    var msg = 'GeoIP-Datenbank wurde aktualisiert.';
                    if (data.response) {
                        msg += '<br/><br/><code style="font-size:11px;">' + data.response + '</code>';
                    }
                    BootstrapDialog.show({
                        type: BootstrapDialog.TYPE_SUCCESS,
                        title: 'GeoIP Update',
                        message: msg,
                        buttons: [{
                            label: 'OK',
                            action: function(dialogRef){ dialogRef.close(); }
                        }]
                    });
                } else {
                    BootstrapDialog.show({
                        type: BootstrapDialog.TYPE_DANGER,
                        title: 'GeoIP Update',
                        message: 'Fehler beim GeoIP-Download. Prüfen Sie:<br/><code>/var/log/tcpgeo.log</code><br/><br/>Ist der MaxMind License Key korrekt eingetragen?',
                        buttons: [{
                            label: 'OK',
                            action: function(dialogRef){ dialogRef.close(); }
                        }]
                    });
                }
            });
        });
    });
</script>

<div class="content-box" style="padding: 10px;">
    <ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
        <li class="active"><a data-toggle="tab" href="#general">{{ lang._('Allgemein') }}</a></li>
        <li><a data-toggle="tab" href="#portcolors">{{ lang._('Port-Farben') }}</a></li>
    </ul>

    <div class="tab-content content-box">
        <!-- General Settings Tab -->
        <div id="general" class="tab-pane fade in active">
            {{ partial("layout_partials/base_form",['fields':generalForm,'id':'frm_GeneralSettings'])}}

            <div class="col-md-12" style="margin-top: 15px;">
                <div class="alert alert-info" style="margin-bottom: 10px;">
                    <i class="fa fa-info-circle"></i>
                    <strong>Hinweis:</strong> Der Globus wird auf der gewählten Schnittstelle unter dem angegebenen Port erreichbar sein.
                    Beispiel: Wenn LAN (192.168.1.1) und Port 3333 gewählt werden, ist der Globus unter
                    <code>http://192.168.1.1:3333</code> bzw. <code>https://192.168.1.1:3333</code> (bei aktiviertem HTTPS) erreichbar.
                </div>
                <div class="alert alert-warning" style="margin-bottom: 10px;">
                    <i class="fa fa-key"></i>
                    <strong>GeoIP:</strong> Für die GeoIP-Auflösung wird ein kostenloser MaxMind License Key benötigt.
                    Registrierung unter <a href="https://www.maxmind.com/en/geolite2/signup" target="_blank">maxmind.com</a>.
                    Die Datenbank wird wöchentlich automatisch aktualisiert.
                </div>
            </div>
        </div>

        <!-- Port Colors Tab -->
        <div id="portcolors" class="tab-pane fade">
            <div class="col-md-12" style="margin-bottom: 10px;">
                <div class="alert alert-info">
                    <i class="fa fa-paint-brush"></i>
                    <strong>Port-Farben:</strong> Ordnen Sie jedem Port eine individuelle Farbe zu.
                    Der Globus stellt Verbindungen auf diesen Ports mit der zugewiesenen Farbe dar.
                    Eingehender Traffic zeigt die zugewiesene Farbe, ausgehender Traffic wird zusätzlich multicolor dargestellt.
                    Ports ohne Farbzuordnung werden in Cyan angezeigt.
                </div>
            </div>

            <table id="grid-portcolors" class="table table-condensed table-hover table-striped"
                   data-editDialog="DialogPortcolor"
                   data-editAlert="PortcolorChangeMessage">
                <thead>
                    <tr>
                        <th data-column-id="uuid" data-type="string" data-identifier="true" data-visible="false">{{ lang._('ID') }}</th>
                        <th data-column-id="enabled" data-width="6em" data-type="string" data-formatter="rowtoggle">{{ lang._('Aktiv') }}</th>
                        <th data-column-id="port" data-type="string" data-width="8em">{{ lang._('Port') }}</th>
                        <th data-column-id="color" data-type="string" data-formatter="colorpreview">{{ lang._('Farbe') }}</th>
                        <th data-column-id="label" data-type="string">{{ lang._('Bezeichnung') }}</th>
                        <th data-column-id="commands" data-width="7em" data-formatter="commands" data-sortable="false">{{ lang._('Aktionen') }}</th>
                    </tr>
                </thead>
                <tbody>
                </tbody>
                <tfoot>
                    <tr>
                        <td></td>
                        <td>
                            <button data-action="add" type="button" class="btn btn-xs btn-primary">
                                <span class="fa fa-fw fa-plus"></span>
                            </button>
                            <button data-action="deleteSelected" type="button" class="btn btn-xs btn-default">
                                <span class="fa fa-fw fa-trash-o"></span>
                            </button>
                        </td>
                    </tr>
                </tfoot>
            </table>

            <div id="PortcolorChangeMessage" class="alert alert-info" style="display: none;">
                {{ lang._('Änderungen müssen nach dem Speichern mit "Anwenden" übernommen werden.') }}
            </div>
        </div>
    </div>

    <!-- Save & Apply button + Service Control -->
    <div class="col-md-12" style="margin-top: 15px;">
        <hr/>
        <button class="btn btn-primary" id="saveAct"
                data-endpoint='/api/tcpgeo/service/reconfigure'
                data-label="{{ lang._('Speichern & Anwenden') }}"
                data-error-title="{{ lang._('Fehler beim Konfigurieren von TCPGeo') }}"
                type="button">
        </button>
        <button class="btn btn-default" id="downloadGeoIP" style="margin-left: 10px;">
            <i class="fa fa-download"></i> {{ lang._('GeoIP aktualisieren') }}
        </button>
    </div>
</div>

<!-- Port-Color Edit Dialog -->
{{ partial("layout_partials/base_dialog",['fields':portcolorForm,'id':'DialogPortcolor','label':lang._('Port-Farbe bearbeiten')])}}
