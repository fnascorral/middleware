// Update
// =======
//

"use strict";

import React from "react";

import UpdateMiddleware from "../../middleware/UpdateMiddleware";

import Icon from "../../components/Icon";

import ConfDialog from "../../components/common/ConfDialog";

const Update = React.createClass({
  handleupdatenowbutton: function () {
    UpdateMiddleware.updatenow();
  }

  , render: function () {
    var updateText = (  <div style = { {margin: "5px"
                                    , cursor: "pointer"} }>
                            <Icon glyph = "bomb"
                             icoSize = "4em" />
                            <br />
                            Update
                          </div> );
    var updateprops = {};
    updateprops.dataText = updateText;
    updateprops.title = "Confirm Update";
    updateprops.bodyText = "Freenas will now Update"
    updateprops.callFunc  = this.handleupdatenowbutton;

    return (
      <main>
        <h2>Update</h2>
        <ConfDialog {...updateprops}/>
      </main>
    );
  }
});

export default Update;
