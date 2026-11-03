# Shape contract for exporting Seurat's weighted-nearest-neighbor graph.

wnn_neighbor_shapes_match <- function(nn_index, nn_distance, cell_count) {
  index_shape <- dim(nn_index)
  distance_shape <- dim(nn_distance)
  valid_cell_count <- length(cell_count) == 1L &&
    is.numeric(cell_count) &&
    is.finite(cell_count) &&
    cell_count >= 0 &&
    cell_count == floor(cell_count)
  valid_shapes <- length(index_shape) == 2L &&
    length(distance_shape) == 2L &&
    all(is.finite(index_shape)) &&
    all(is.finite(distance_shape))
  isTRUE(valid_cell_count) &&
    isTRUE(valid_shapes) &&
    isTRUE(index_shape[1] == cell_count) &&
    isTRUE(all(index_shape == distance_shape))
}

assert_wnn_neighbor_shapes <- function(nn_index, nn_distance, cell_count) {
  if (!wnn_neighbor_shapes_match(nn_index, nn_distance, cell_count)) {
    stop("weighted.nn export shape does not match the joint cells")
  }
  invisible(TRUE)
}
