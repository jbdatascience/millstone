/** * @fileoverview Sub-view displaying Variants.
 */


gd.TabAnalyzeSubviewVariants = gd.TabAnalyzeSubviewAbstractBase.extend(
{
  /** @override */
  initialize: function() {
    // The DataTableComponent currently in view.
    this.datatableComponent = null;

    // Current list of variants being displayed.
    this.variantList = null;

    // Datatable field config.
    this.fieldConfig = null;

    // List of variant sets belonging to the user.
    this.variantSetList = null;

    // Map of variant keys.
    this.variantKeyMap = null;

    // Keys that should be visible.
    // When null, we are just showing the default.
    this.visibleKeyNames = null;

    // Check whether a filter is set on the view.
    var paramObject = gd.Util.getQueryStringAsObject();
    var maybeQueryString = window.location.search;
    if ('filter' in paramObject) {
      var filterString = paramObject['filter'];
      this.model.set('filterString', filterString);
    } else {
      this.model.set('filterString', '');
    }

    // Set the view to cast as default.
    // TODO: Url support.
    this.model.set('is_melted', false);
    if ('melt' in paramObject) {
      var isMelted = (paramObject['melt'] == '1');
      this.model.set('is_melted', isMelted);
    }

    gd.TabAnalyzeSubviewAbstractBase.prototype.initialize.call(this);
  },


  /** @override */
  listenToControls: function() {
    // Fill in the filter if relevant.
    $('#gd-new-filter-input').val(this.model.get('filterString'));

    // Decorate the cast/melt toggle.
    if (this.model.get('is_melted')) {
      $('#gd-snp-filter-melt-toggle-melt').addClass('active');
    } else {
      $('#gd-snp-filter-melt-toggle-cast').addClass('active');
    }

    // Register listeners.
    $('#gd-filter-box-apply-btn').click(
        _.bind(this.handleApplyFilterClick, this));
    $('.gd-snp-filter-melt-toggle').click(
        _.bind(this.handleMeltedToggleClick, this));
    $('#gd-filter-field-select-btn').click(
        _.bind(this.handleShowFieldSelect, this));
    $('#gd-snp-filter-error-close-btn').click(
        _.bind(this.handleErrorAlertClose, this));
    $('#gd-new-filter-input').keypress(
        _.bind(this.handleFilterInputKeypress, this));
  },


  /** @override */
  renderDatatable: function() {
    // Always start this flow by checking whether the materialized view
    // is valid. Then do the rest of the create after.
    this.prepareMaterializedView(
        _.bind(this.afterMaterializedViewReady, this));
  },


  afterMaterializedViewReady: function() {
    // Recreate the initial DataTable.
    this.createDatatableComponent();

    // Kick off request to update the Variant data being displayed.
    this.updateVariantList();

  },


  /**
   * Checks if the underlying materialized view needs to be updated
   * and kick off a whole ui affordance to manage that.
   */
  prepareMaterializedView: function(callback) {
    // First make a request to check if needs to be refreshed.
    var requestData = {
      'refGenomeUid': this.model.get('refGenomeUid'),
    };
    $.get('/_/variants/is_materialized_view_valid', requestData,
        _.bind(function(responseData) {
          if (responseData.isValid) {
            callback.call();
          } else {
            this.handleRefreshMaterializedView(callback);
          }
        }, this));
  },


  /** Redraws the datatable based on the selection. */
  createDatatableComponent: function() {
    this.datatableComponent = new gd.ServerSideDataTableComponent({
        el: $('#gd-datatable-hook'),
        serverTarget: '/_/variants',
        controlsTemplate: '/_/templates/variant_filter_controls',
        requestData: {'refGenomeUid': this.model.get('refGenomeUid')},
        serverParamsInjector: _.bind(
            this.addFilterDataServerSideRequest, this)
    });

    // Listen to events from the DataTable to provide appropriate UI affordance.
    this.listenTo(this.datatableComponent, 'START_LOADING',
        _.bind(this.setUIStartLoadingState, this));
    this.listenTo(this.datatableComponent, 'DONE_LOADING',
        _.bind(this.setUIDoneLoadingState, this));
    this.listenTo(this.datatableComponent, 'DONE_CONTROLS_REDRAW',
        _.bind(this.listenToControls, this));
    this.listenTo(this.datatableComponent, 'DATA_FETCH_ERROR',
        _.bind(this.handleGetVariantListError, this));
  },


  /**
   * Updates the array of data representing GET params to be sent to the
   * server by DataTables. See jquery.dataTables.js.
   *
   * @param {Array.<Object.<string, string>>} aoData Array of objects with keys
   *     'name' and 'value'. The array is passed by reference so this method
   *      must mutate this array.
   */
  addFilterDataServerSideRequest: function(aoData) {
    var requestData = {
      'projectUid': this.model.get('project').uid,
      'refGenomeUid': this.model.get('refGenomeUid'),
      'variantFilterString': $('#gd-new-filter-input').val(),
      'melt': this.model.get('is_melted') ? 1 : 0
    };

    _.each(_.pairs(requestData), function(pair) {
      aoData.push({'name': pair[0], 'value': pair[1]});
    })
  },


  /** Provide affordance while doing Variant query. */
  setUIStartLoadingState: function() {
    $('#gd-datatable-hook-datatable_wrapper').css('opacity', 0.5);

    this.loadingSpinner = new gd.Spinner();
    this.loadingSpinner.spin()
  },


  /** Reset UI changes after loading complete.. */
  setUIDoneLoadingState: function() {
    $('#gd-datatable-hook-datatable_wrapper').css('opacity', 1);
    if (this.loadingSpinner) {
      this.loadingSpinner.stop();
    }
  },


  /** Handles the server response containing Variants data. */
  handleGetVariantListResponse: function(response) {
    if ('error' in response) {
      this.handleGetVariantListError(response.error);
      return;
    }

    // Parse Variant data.
    var variantListData = JSON.parse(response.variant_list_json);
    if (variantListData.obj_list.length) {
      this.variantList = variantListData.obj_list;
      this.fieldConfig = variantListData.field_config;
    } else {
      this.variantList = [];
      this.fieldConfig = {};
    }

    var numTotalVariants = Number(response.num_total_variants);

    // Parse VariantSet data.
    this.variantSetList = JSON.parse(response.variant_set_list_json).obj_list;

    // Grab the field key map data.
    this.variantKeyMap = JSON.parse(response.variant_key_filter_map_json);

    // Redraw the datatable.
    this.datatableComponent.update(this.variantList, this.fieldConfig,
        numTotalVariants);

    // Does this need to be called every time?
    this.drawDropdowns();

    // Reset the ui.
    this.setUIDoneLoadingState();
  },


  handleGetVariantListError: function(errorMsg) {
    this.setUIDoneLoadingState();
    $('#gd-snp-filter-error-msg').text(errorMsg);
    $('#gd-snp-filter-error').show();
  },


  /**
   * Hides the alert box.
   *
   * Previously, we were adding Bootstrap's data-dismiss="alert" to the dom
   * but that was causing the entire element to be deleted so that subsequent
   * alerts wouldn't show.
   */
  handleErrorAlertClose: function() {
    $('#gd-snp-filter-error').hide();
  },

  /** Handles keypress on filter input. */
  handleFilterInputKeypress: function(e) {
    // If enter key, submit.
    var code = e.keyCode || e.which;
    if(code == 13) {
      this.handleApplyFilterClick();
    }
  },

  /**
   * Creates the modal for selecting which fields are being displayed and
   * shows it.
   */
  handleShowFieldSelect: function() {
    var variantFieldSelectModel = new Backbone.Model({
        'variantKeyMap': this.variantKeyMap
    });
    this.variantFieldSelect = new gd.VariantFieldSelectComponent(
        {'model': variantFieldSelectModel});
    this.listenTo(this.variantFieldSelect, 'updateSelectedFields',
        _.bind(this.handleUpdateSelectedFields, this));
  },


  /**
   * Sends a request to the server to refresh the materialized data table and
   * reloads the page on success.
   */
  handleRefreshMaterializedView: function(onSuccess) {
    this.setUIStartLoadingState();

    var requestData = {
      'refGenomeUid': this.model.get('refGenomeUid'),
    };
    $.get('/_/variants/refresh_materialized_variant_table', requestData,
        _.bind(function(data) {
          this.setUIDoneLoadingState();
          onSuccess();
        }, this));
  },


  /** Parses the form files and prepares the data. */
  prepareRequestDataForEmptyVariantSet: function(formId) {
    var requestData = {}
    var formInputs = $('#' + formId + ' :input');
    _.each(formInputs, function(inputObj) {
      requestData[inputObj.name] = inputObj.value;
    });
    return requestData;
  },

  /** Validates the request to be sent to the server. */
  validateCreateEmptyVariantSetRequestData: function(requestData) {
    if (!requestData['refGenomeUid'].length) {
      alert('Please select a reference genome.');
      return false;
    }

    if (!requestData['variantSetName'].length) {
      alert('Please enter a variant set name.');
      return false;
    }

    return true;
  },

  /** Handles creating a new empty Variant set. */
  handleFormSubmitCreateEmptyVariantSet: function() {

    // Parse the inputs.
    var requestData = this.prepareRequestDataForEmptyVariantSet('gd-variant-set-form-empty');
    // Validate the request client-side.
    if (!this.validateCreateEmptyVariantSetRequestData(requestData)) {
      return;
    }

    // Make the request.
    $.post('/_/sets/create', requestData, _.bind(function(responseData) {
      // Check for error and show in ui. Don't reload the page.
      if (responseData.error.length) {
        alert('Error creating variant set: ' + responseData.error);
        return;
      }

      // The common data.
      var postData = {
          refGenomeUid: this.model.get('refGenomeUid'),
          variantSetAction: 'add',
          variantSetUid: responseData['variantSetUid']
      };

      this.modifyVariantSetMembership(postData);

      $('#gd-variant-set-form-empty-submit').disabled = true;
      $('#gd-create-empty-variant-set-form').modal('hide');
    }, this));
  },


  /** Creates a new empty variant set **/
  handleCreateNewEmptyVariantSet: function() {

    var requestData = {
      'refGenomeUid': this.model.get('refGenomeUid'),
    }

    $.get('/_/templates/create_new_empty_variant_set', requestData, _.bind(
      function (data) {

        $("#gd-datatable-hook").append(data);
        $('#gd-create-empty-variant-set-form').modal('show');

        $('#gd-variant-set-form-empty-submit').click(
          _.bind(this.handleFormSubmitCreateEmptyVariantSet, this));

    }, this));
  },


  /**
   * Starts a download of selected Variants .csv format.
   */
  handleExportCsv: function() {
    // First make sure there is somsething to export. Either rows are checked,
    // or select all is in place.
    var checkedRowUidList = this.datatableComponent.getCheckedRowUids();
    if (!checkedRowUidList.length &&
        !this.datatableComponent.isAllMatchingFilterSelected()) {
      alert('Please select rows to export, or select all.');
      return;
    }

    var formJqueryObj = $('#gd-filter-export-csv-form');

    // Reset the form html
    formJqueryObj.empty();

    // Append the form fields.
    this._appendInputFieldToForm(formJqueryObj, 'ref_genome_uid',
        this.model.get('refGenomeUid'));
    this._appendInputFieldToForm(formJqueryObj, 'filter_string',
        this.model.get('filterString'));
    if (this.datatableComponent.isAllMatchingFilterSelected()) {
      this._appendInputFieldToForm(formJqueryObj, 'get_all_matching_filter', 1);
    } else {
      alert('Row-specific export coming soon. Please select all and try again.');
      return;
    }

    // Submit the form. This cause a download to start.
    formJqueryObj.submit();
  },

  /** Helper method to append input value to form. */
  _appendInputFieldToForm: function(formJqueryObj, name, value) {
    formJqueryObj.append(_.template(
        '<input type="hidden" name="<%= name %>" value="<%= value %>">',
        {name: name, value: value}
    ));
  },

  /** Handles an update event from the field select component. */
  handleUpdateSelectedFields: function(selectedKeyNames) {
    // Hide the modal.
    this.variantFieldSelect.hide();

    // Update the attribute that stores the visible field names.
    this.visibleKeyNames = selectedKeyNames;

    // Kick off a request to update the view.
    this.updateVariantList();
  },


  /** Kicks off the process for updating the list of displayed variants. */
  updateVariantList: function() {
    // Update the url with the filter.
    var updatedPath = gd.Util.updateQueryStringParameter(
        gd.Util.getFullPath(), 'filter', this.model.get('filterString'));
    updatedPath = gd.Util.updateQueryStringParameter(
        updatedPath, 'melt', this.model.get('is_melted') ? '1' : '0');
    this.trigger('NAVIGATE', {'path': updatedPath});

    // Hide any alert.
    this.handleErrorAlertClose();

    // Update the UI to show that the new Variant list is loading.
    this.setUIStartLoadingState();

    var requestData = {
      'projectUid': this.model.get('project').uid,
      'refGenomeUid': this.model.get('refGenomeUid'),
      'variantFilterString': this.model.get('filterString'),
      'melt': this.model.get('is_melted') ? 1 : 0
    };

    if (this.visibleKeyNames) {
      requestData['visibleKeyNames'] = JSON.stringify(this.visibleKeyNames);
    }

    $.get('/_/variants', requestData,
        _.bind(this.handleGetVariantListResponse, this));
  },


  /** Handles a click on one of the melted toggle buttons. */
  handleApplyFilterClick: function(e) {
    // Update the model.
    this.model.set('filterString', $('#gd-new-filter-input').val());
    this.updateVariantList();
  },


  /** Handles a click on one of the melted toggle buttons. */
  handleMeltedToggleClick: function(e) {
    // Update the model.
    this.setUIDoneLoadingState();
    this.model.set('is_melted', Boolean($(e.target).data('melted')));
    this.model.set('filterString', $('#gd-new-filter-input').val());
    this.updateVariantList();
  },


  /* Adds options to the variant set dropdown menu. */
  drawDropdowns: function() {
    // Create 'add selected to set' button, display list of available sets
    this.addVariantSetDropdownSubmenu(
        this.variantSetList, 'add', 'Add to set');
    this.addVariantSetDropdownSubmenu(
        this.variantSetList, 'remove', 'Remove from set');
    $('.gd-id-variant-set-action').click(
        _.bind(this.handleVariantSetActionClick, this));

    // Create 'Create new variant set' button
    var createNewVariantSetOption =
        '<a href="#" class="gd-id-add-to-new-variant-set">Add to new variant set</a>';
    this.datatableComponent.addDropdownOption(createNewVariantSetOption, '');
    $('.gd-id-add-to-new-variant-set').click(
        _.bind(this.handleCreateNewEmptyVariantSet, this));

    // Create option to export selected.
    var exportOptionHtml =
        '<a href="#" class="gd-id-export-selected">Export as csv</a>';
    this.datatableComponent.addDropdownOption(exportOptionHtml, '');
    $('.gd-id-export-selected').click(
        _.bind(this.handleExportCsv, this));
  },


  /*
   * Create a dropdown submenu with a list of all variant sets, to which
   * selected variants will be added.
   */
  addVariantSetDropdownSubmenu: function(allVariantSets, varSetAction,
      varSetActionText) {
    var setListHTML = (
      '<li class="dropdown-submenu">' +
        '<a tabindex="-1" href="#">' + varSetActionText + '</a>' +
        '<ul class="dropdown-menu">' +
          _.map(allVariantSets,
            function(variantSet) {
              return this.addIndividualSetListItem(variantSet, varSetAction);
            }, this).join('') +
        '</ul>' +
      '</li>');

    this.datatableComponent.addDropdownOption(setListHTML, '');
  },


  /**
   * Create a single html list object for a sub-dropdown set option.
   *
   * @param variantSet The variant set dictionary object from the
   *     django adapter.
   * @param variantSetAction The action to perform, either 'add' or 'remove'
   */
  addIndividualSetListItem: function(variantSet, variantSetAction) {
    return (
      '<li><a class="gd-id-variant-set-action" ' +
          'data-variant-set-uid="' + variantSet.uid + '" ' +
          'data-variant-set-action="' + variantSetAction +
          '" href="#">' +
        variantSet.label +
      '</a></li>');
  },


  /** Handles a click on a variant set action button. */
  handleVariantSetActionClick: function(ev) {

    // The common data.
    var postData = {
        refGenomeUid: this.model.get('refGenomeUid'),
        variantSetAction: $(ev.target).data('variant-set-action'),
        variantSetUid: $(ev.target).data('variant-set-uid')
    };

    this.modifyVariantSetMembership(postData);
  },


  /** 
   * Modifies set membership, posts to server. 
   *
   * @param partialPostData. Data that describes the action to be taken.
   *    Other data to be added to this request internally. 
   *    Required keys:
   *        * refGenomeUid
   *        * variantSetAction
   *        * variantSetUid
   */
  modifyVariantSetMembership: function(partialPostData){

    var postUrl = '/_/variants/modify_set_membership';

    var postData = _.clone(partialPostData);

    // The appropriate selected row data, or the filter.
    if (this.datatableComponent.isAllMatchingFilterSelected()) {
      postData.isAllMatchingFilterSelected = true;
      postData.filterString = this.model.get('filterString');
      postData.isMelted = this.model.get('is_melted');
    } else {
      postData.isAllMatchingFilterSelected = false;
      postData.variantUidList = this.datatableComponent.getCheckedRowUids();
    }

    var validationResult = this.validatePostData(postData);
    if (!validationResult.is_success) {
      this.showAlertMessage(validationResult.error_msg, 'error');
      return;
    }

    var onSuccess = _.bind(function(response_json) {
      var response = JSON.parse(response_json);
      if (response.alert_type == 'error') {
        alert(response.alert_msg);
        return;
      }

      this.updateVariantList();
    }, this);

    // Execute the post. Should return a redirect response.
    $.post(postUrl, JSON.stringify(postData), onSuccess);
  },


  /**
   * Validate the post data before submitting.
   * @param {object} postData The creation data to be posted to the server.
   * @return {object} Response with keys:
   *     * is_success {boolean} Whether validation succeded.
   *     * error_msg {string} Human-readable description of the error.
   */
  validatePostData: function(postData) {
    if (postData.isAllMatchingFilterSelected &&
        'isAllMatchingFilterSelected' in postData) {
      return {
          is_success: true,
          error_msg: ''
      };
    }

    if (!postData.variantUidList.length) {
      return {
          is_success: false,
          error_msg: 'Please select at least one variant.'
      };
    }

    return {
          is_success: true,
          error_msg: ''
    };
  },


  /**
   * Shows an alert message.
   *
   * TODO: Add back the DOM hooks to make this work.
   */
  showAlertMessage: function(msg, alertType) {
    $('#gd-variant-set-action-submit-alert-msg').text(msg);
    $('#gd-variant-set-action-submit-alert').removeClass(
        'alert-warn alert-info alert-warn');
    $('#gd-variant-set-action-submit-alert').addClass('alert-' + alertType);
    $('#gd-variant-set-action-submit-alert').show();
  },


  /** Properly clean up this component. Inheriting classes should override. */
  destroy: function() {
    if (this.datatableComponent) {
      this.datatableComponent.destroy()
    }
    this.datatableComponent = null;
  }
});
