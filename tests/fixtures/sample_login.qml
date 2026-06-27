import QtQuick
import QtQuick.Layouts

Item {
    id: root
    property string fontFamily: "Lato"
    property string fontWeight: "Normal"
    property string passwordCharacter: "•"

    function format(d) { return Qt.formatDateTime(d, "hh:mm") }
}
