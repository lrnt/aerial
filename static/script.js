var markers = {};
var lines = {};
var iconUrls = {
    'M': {
        'regular': '/static/images/metro.png',
        'retina': '/static/images/metro-2x.png/'
    },
    'T': {
        'regular': '/static/images/tram.png',
        'retina': '/static/images/tram-2x.png/'
    },
    'B': {
        'regular': '/static/images/bus.png',
        'retina': '/static/images/bus-2x.png/'
    }
};

function createMarker(lineId, iti, mode, stop, lat, lon)
{
    var line = lines[lineId];
    var mid = lineId + '.' + iti + ':' + stop;
    var icon = L.icon({
        'iconUrl': iconUrls[mode]['regular'],
        'iconRetinaUrl': iconUrls[mode]['retina'],
        'iconSize': [16, 16]
    });

    var m = L.Marker.movingMarker([[lat, lon]], [0], {icon: icon});

    m.bindPopup(
        '<div class="line">' + 
        '<div class="number" style="background-color:#' + line['bgcolor'] + ';' +
                                   'color:# ' + line['fgcolor'] + ';">' +
        line['id'] +
        '</div>' +
        '<div class="destination">' +
        line['destination'+iti] +
        '</div>' +
        '</div>'
    );

    markers[mid] = m;
    return m;
}

$(document).ready(function() {
    var map = L.map('map').setView([50.8480, 4.3633], 13);
    L.tileLayer('http://{s}.tile.osm.org/{z}/{x}/{y}.png').addTo(map);

    $.ajax({
        url: '/lines/',
        dataType: 'json',
        async: false,
        success: function(data) {
            lines = data;
        }
    });

    $.ajax({
        url: '/present/',
        dataType: 'json',
        async: false,
        success: function(data) {
            $.each(data, function(i, present){
                createMarker(
                    present['line'],
                    present['route']['iti'],
                    lines[present['line']]['mode'],
                    present['stop']['id'],
                    present['stop']['latitude'],
                    present['stop']['longitude']
                ).addTo(map);
            });
        }
    });

    var ws = new WebSocket('ws://' + window.location.host + '/socket/');
    ws.onmessage = function(e) {
        var data = $.parseJSON(e.data)

        var route = data['route'].split('.')
        var lineId = route[0];
        var iti = route[1];
        var line = lines[lineId];

        var origin = data['origin'];
        var destination = data['destination'];

        var oid = data['route'] + ':' + origin['id'];
        var did = data['route'] + ':' + destination['id'];

        // New marker
        if (origin['id'] == '-1')
        {
            createMarker(
                lineId,
                iti,
                line['mode'],
                destination['id'],
                destination['lat'],
                destination['lon']
            ).addTo(map);
            return;
        }

        // Remove marker
        if (destination['id'] == '-1')
        {
            map.removeLayer(markers[oid]);
            delete markers[oid];
            return;
        }

        // Move marker
        var m = markers[oid];
        m.moveTo([destination['lat'], destination['lon']], 7000);
        markers[did] = m;
        delete markers[oid];
    };
});
